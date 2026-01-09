"""
Wrapper functions for tracking MCP tool calls.
Built on OpenTelemetry for distributed tracing.
"""

import functools
import json
import uuid
import time
from datetime import datetime, timezone
from fastmcp import Context
import inspect

try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
    from opentelemetry.semconv.trace import SpanAttributes
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False


def create_async_wrapper(observer, func, sig, has_context: bool, track_io: bool = False):
    """Create an async wrapper for tracking function calls with OpenTelemetry."""
    
    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        call_id = str(uuid.uuid4())
        func_name = getattr(func, '__name__', 'unknown_function')
        
        # Start OpenTelemetry span
        span = None
        if OTEL_AVAILABLE and observer.tracer:
            span = observer.tracer.start_span(
                f"mcp.tool.{func_name}",
                attributes={
                    SpanAttributes.RPC_METHOD: func_name,
                    SpanAttributes.RPC_SYSTEM: "mcp",
                    "mcp.call_id": call_id,
                    "mcp.track_io": track_io,
                }
            )
        
        # Extract context if present
        context = None
        session_id = None
        can_store_full = None

        if has_context:
            # Look for context in kwargs first
            context = kwargs.get('ctx') or kwargs.get('context')

            # If not in kwargs, try to bind args to find context
            if context is None:
                try:
                    bound_args = sig.bind(*args, **kwargs)
                    bound_args.apply_defaults()
                    context = bound_args.arguments.get('ctx') or bound_args.arguments.get('context')
                except Exception:
                    pass
            observer.logger.info(f"[TRACK] Extracted context: {context}")
            if context and hasattr(context, 'session_id'):
                session_id = context.session_id
                if span:
                    span.set_attribute("mcp.session_id", session_id)

        # Resolve or create run_id (immediately after extracting context)
        run_id = call_id  # Default: synthetic run_id (one run per call)
        is_new_run = False
        if observer.run_manager and session_id:
            try:
                run_id, is_new_run = await observer.run_manager.resolve_or_create_run(
                    session_id=session_id,
                    timestamp=datetime.now(timezone.utc)
                )
                observer.logger.info(
                    f"[TRACK] Run ID: {run_id} ({'new' if is_new_run else 'existing'})"
                )
                if span:
                    span.set_attribute("mcp.run_id", run_id)
                    span.set_attribute("mcp.run_is_new", is_new_run)
            except Exception as run_error:
                observer.logger.warning(f"Failed to resolve run: {run_error}, using synthetic run_id")
                # Fall back to synthetic run_id (call_id)

        # Create clean payload for database (exclude Context objects)
        clean_args = []
        for arg in args:
            if not isinstance(arg, Context):
                try:
                    clean_args.append(arg)
                except:
                    clean_args.append(str(arg))
        
        clean_kwargs = {}
        for k, v in kwargs.items():
            if k not in ['ctx', 'context'] and not isinstance(v, Context):
                try:
                    clean_kwargs[k] = v
                except:
                    clean_kwargs[k] = str(v)
        
        input_data = {
            "args": clean_args,
            "kwargs": clean_kwargs
        }
        
        context_data = {
            "session_id": session_id,
            "has_context": context is not None,
            "function_signature": str(sig),
            "run_id": run_id
        }
        
        # Add input info to span
        if span:
            span.set_attribute("mcp.args_count", len(clean_args))
            span.set_attribute("mcp.kwargs_count", len(clean_kwargs))
        
        # Print tracking information
        observer.logger.info(f"[TRACK] Function: {func_name}")
        observer.logger.info(f"[TRACK] Call ID: {call_id}")
        observer.logger.info(f"[TRACK] Input: {json.dumps(input_data, indent=2, default=str)}")
        if session_id:
            observer.logger.info(f"[TRACK] Session ID: {session_id}")
        
        result = None
        error = None
        
        try:
            # Call the original function with original arguments
            start_time = datetime.now(timezone.utc)
            start_perf = time.perf_counter()
            result = await func(*args, **kwargs)
            end_time = datetime.now(timezone.utc)
            end_perf = time.perf_counter()
            latency_ms = int((end_perf - start_perf) * 1000)

            # Update span with success
            if span:
                span.set_status(Status(StatusCode.OK))
                span.set_attribute("mcp.latency_ms", latency_ms)
                span.set_attribute("mcp.status", "ok")
            
            # Record metrics
            if observer.call_counter:
                observer.call_counter.add(1, {
                    "tool_name": func_name,
                    "status": "ok",
                    "session_id": session_id or "none"
                })
            if observer.call_duration:
                observer.call_duration.record(latency_ms, {
                    "tool_name": func_name,
                    "status": "ok"
                })
            
            # Check if we should store full I/O
            can_store_full = False
            if track_io:
                # Check tracking policy via API (replaces _upsert_tool + _check_full_tracking)
                can_store_full = observer._check_tracking_policy(func_name, full_tracking_allowed=True)
                observer.logger.info(f"[TRACK] Full I/O tracking allowed: {can_store_full}")
                if span:
                    span.set_attribute("mcp.full_tracking", can_store_full)
            
            # Record successful call asynchronously (non-blocking)
            await observer.record_call(
                call_id=call_id,
                tool_name=func_name,
                input_data=input_data,
                output_data=result,
                context_data=context_data,
                started_at=start_time,
                completed_at=end_time,
                latency_ms=latency_ms,
                session_id=session_id,
                can_store_full=can_store_full,
                json_in=input_data if can_store_full else None,
                json_out=result if can_store_full else None,
                full_tracking_allowed=track_io
            )
            
            observer.logger.info(f"[TRACK] Result: {json.dumps(str(result), indent=2)}")
            observer.logger.info(f"[TRACK] Duration: {latency_ms}ms")
            observer.logger.info(f"[TRACK] Success: true")
            
            return result
            
        except Exception as e:
            error = e
            end_time = datetime.now(timezone.utc)
            end_perf = time.perf_counter()
            latency_ms = int((end_perf - start_perf) * 1000)

            # Update span with error
            if span:
                span.set_status(Status(StatusCode.ERROR, str(error)))
                span.set_attribute("mcp.latency_ms", latency_ms)
                span.set_attribute("mcp.status", "error")
                span.set_attribute("mcp.error_type", type(error).__name__)
                span.record_exception(error)
            
            # Record error metrics
            if observer.error_counter:
                observer.error_counter.add(1, {
                    "tool_name": func_name,
                    "error_type": type(error).__name__,
                    "session_id": session_id or "none"
                })
            if observer.call_duration:
                observer.call_duration.record(latency_ms, {
                    "tool_name": func_name,
                    "status": "error"
                })
            
            # Check if we should store full I/O for errors
            can_store_full = False
            if track_io:
                can_store_full = observer._check_tracking_policy(func_name, full_tracking_allowed=True)
            
            # Record failed call asynchronously (non-blocking)
            await observer.record_call(
                call_id=call_id,
                tool_name=func_name,
                input_data=input_data,
                context_data=context_data,
                started_at=start_time,
                completed_at=end_time,
                error=error,
                latency_ms=latency_ms,
                session_id=session_id,
                can_store_full=can_store_full,
                json_in=input_data if can_store_full else None,
                json_out={"error": str(error)} if can_store_full else None,
                full_tracking_allowed=track_io
            )

            observer.logger.info(f"[TRACK] Error: {json.dumps(str(error), indent=2)}")
            observer.logger.info(f"[TRACK] Duration: {latency_ms}ms")
            observer.logger.info(f"[TRACK] Success: false")

            raise error
        finally:
            # End span
            if span:
                span.end()
    
    return async_wrapper


def create_sync_wrapper(observer, func, sig, has_context: bool, track_io: bool = False):
    """Create a sync wrapper for tracking function calls."""

    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        call_id = str(uuid.uuid4())
        func_name = getattr(func, '__name__', 'unknown_function')

        # Start OpenTelemetry span
        span = None
        if OTEL_AVAILABLE and observer.tracer:
            span = observer.tracer.start_span(
                f"mcp.tool.{func_name}",
                attributes={
                    SpanAttributes.RPC_METHOD: func_name,
                    SpanAttributes.RPC_SYSTEM: "mcp",
                    "mcp.call_id": call_id,
                    "mcp.track_io": track_io,
                }
            )

        # Extract context if present
        context = None
        session_id = None

        if has_context:
            # Look for context in kwargs first
            context = kwargs.get('ctx') or kwargs.get('context')

            # If not in kwargs, try to bind args to find context
            if context is None:
                try:
                    bound_args = sig.bind(*args, **kwargs)
                    bound_args.apply_defaults()
                    context = bound_args.arguments.get('ctx') or bound_args.arguments.get('context')
                except Exception:
                    pass
            observer.logger.info(f"[TRACK] Extracted context: {context}")
            if context and hasattr(context, 'session_id'):
                session_id = context.session_id
                if span:
                    span.set_attribute("mcp.session_id", session_id)

        # Resolve or create run_id (immediately after extracting context)
        run_id = call_id  # Default: synthetic run_id (one run per call)
        is_new_run = False
        if observer.run_manager and session_id:
            try:
                run_id, is_new_run = await observer.run_manager.resolve_or_create_run(
                    session_id=session_id,
                    timestamp=datetime.now(timezone.utc)
                )
                observer.logger.info(
                    f"[TRACK] Run ID: {run_id} ({'new' if is_new_run else 'existing'})"
                )
                if span:
                    span.set_attribute("mcp.run_id", run_id)
                    span.set_attribute("mcp.run_is_new", is_new_run)
            except Exception as run_error:
                observer.logger.warning(f"Failed to resolve run: {run_error}, using synthetic run_id")
                # Fall back to synthetic run_id (call_id)

        # Create clean payload for database (exclude Context objects)
        clean_args = []
        for arg in args:
            if not isinstance(arg, Context):
                try:
                    clean_args.append(arg)
                except:
                    clean_args.append(str(arg))
        
        clean_kwargs = {}
        for k, v in kwargs.items():
            if k not in ['ctx', 'context'] and not isinstance(v, Context):
                try:
                    clean_kwargs[k] = v
                except:
                    clean_kwargs[k] = str(v)
        
        input_data = {
            "args": clean_args,
            "kwargs": clean_kwargs
        }
        
        context_data = {
            "session_id": session_id,
            "has_context": context is not None,
            "function_signature": str(sig),
            "run_id": run_id
        }
        
        # Add input info to span
        if span:
            span.set_attribute("mcp.args_count", len(clean_args))
            span.set_attribute("mcp.kwargs_count", len(clean_kwargs))
        
        # Print tracking information
        observer.logger.info(f"[TRACK] Function: {func_name}")
        observer.logger.info(f"[TRACK] Call ID: {call_id}")
        observer.logger.info(f"[TRACK] Input: {json.dumps(input_data, indent=2, default=str)}")
        if session_id:
            observer.logger.info(f"[TRACK] Session ID: {session_id}")
        
        result = None
        error = None
        
        try:
            # Call the original function with original arguments
            start_time = datetime.now(timezone.utc)
            start_perf = time.perf_counter()
            result = await func(*args, **kwargs)
            end_time = datetime.now(timezone.utc)
            end_perf = time.perf_counter()
            latency_ms = int((end_perf - start_perf) * 1000)

            # Update span with success
            if span:
                span.set_status(Status(StatusCode.OK))
                span.set_attribute("mcp.latency_ms", latency_ms)
                span.set_attribute("mcp.status", "ok")
            
            # Record metrics
            if observer.call_counter:
                observer.call_counter.add(1, {
                    "tool_name": func_name,
                    "status": "ok",
                    "session_id": session_id or "none"
                })
            if observer.call_duration:
                observer.call_duration.record(latency_ms, {
                    "tool_name": func_name,
                    "status": "ok"
                })
            
            # Check if we should store full I/O
            can_store_full = False
            if track_io:
                # Check tracking policy via API (replaces _upsert_tool + _check_full_tracking)
                can_store_full = observer._check_tracking_policy(func_name, full_tracking_allowed=True)
                observer.logger.info(f"[TRACK] Full I/O tracking allowed: {can_store_full}")
                if span:
                    span.set_attribute("mcp.full_tracking", can_store_full)
            
            # Record successful call asynchronously (non-blocking)
            await observer.record_call(
                call_id=call_id,
                tool_name=func_name,
                input_data=input_data,
                output_data=result,
                context_data=context_data,
                started_at=start_time,
                completed_at=end_time,
                latency_ms=latency_ms,
                session_id=session_id,
                can_store_full=can_store_full,
                json_in=input_data if can_store_full else None,
                json_out=result if can_store_full else None,
                full_tracking_allowed=track_io
            )
            
            observer.logger.info(f"[TRACK] Result: {json.dumps(str(result), indent=2)}")
            observer.logger.info(f"[TRACK] Duration: {latency_ms}ms")
            observer.logger.info(f"[TRACK] Success: true")
            
            return result
            
        except Exception as e:
            error = e
            end_time = datetime.now(timezone.utc)
            end_perf = time.perf_counter()
            latency_ms = int((end_perf - start_perf) * 1000)

            # Update span with error
            if span:
                span.set_status(Status(StatusCode.ERROR, str(error)))
                span.set_attribute("mcp.latency_ms", latency_ms)
                span.set_attribute("mcp.status", "error")
                span.set_attribute("mcp.error_type", type(error).__name__)
                span.record_exception(error)
            
            # Record error metrics
            if observer.error_counter:
                observer.error_counter.add(1, {
                    "tool_name": func_name,
                    "error_type": type(error).__name__,
                    "session_id": session_id or "none"
                })
            if observer.call_duration:
                observer.call_duration.record(latency_ms, {
                    "tool_name": func_name,
                    "status": "error"
                })
            
            # Check if we should store full I/O for errors
            can_store_full = False
            if track_io:
                can_store_full = observer._check_tracking_policy(func_name, full_tracking_allowed=True)
            
            # Record failed call
            observer.record_call(
                call_id=call_id,
                tool_name=func_name,
                input_data=input_data,
                context_data=context_data,
                started_at=start_time,
                completed_at=end_time,
                error=error,
                latency_ms=latency_ms,
                session_id=session_id,
                can_store_full=can_store_full,
                json_in=input_data if can_store_full else None,
                json_out={"error": str(error)} if can_store_full else None,
                full_tracking_allowed=track_io
            )

            observer.logger.info(f"[TRACK] Error: {json.dumps(str(error), indent=2)}")
            observer.logger.info(f"[TRACK] Duration: {latency_ms}ms")
            observer.logger.info(f"[TRACK] Success: false")

            raise error
        finally:
            # End span
            if span:
                span.end()
    
    return async_wrapper


def create_async_noauth_wrapper(observer, func, sig):
    """Create an async wrapper for public/no-auth endpoints."""
    
    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        call_id = str(uuid.uuid4())
        start_time = datetime.now(timezone.utc)
        func_name = getattr(func, '__name__', 'unknown_function')
        
        # Create clean payload - no context filtering needed
        input_data = {
            "args": list(args),
            "kwargs": dict(kwargs)
        }
        
        context_data = {
            "requires_auth": False,
            "function_signature": str(sig)
        }
        
        # Print tracking information
        print(f"[TRACK] Function: {func_name} (no auth)")
        print(f"[TRACK] Call ID: {call_id}")
        print(f"[TRACK] Input: {json.dumps(input_data, indent=2, default=str)}")
        
        try:
            result = await func(*args, **kwargs)
            end_time = datetime.now()
            latency_ms = int((end_time - start_time).total_seconds() * 1000)
            
            # Record successful call
            observer.record_call(
                call_id=call_id,
                tool_name=func_name,
                input_data=input_data,
                output_data=result,
                context_data=context_data,
                started_at=start_time,
                completed_at=end_time,
                latency_ms=latency_ms
            )
            
            print(f"[TRACK] Result: {json.dumps(str(result), indent=2)}")
            print(f"[TRACK] Duration: {latency_ms}ms")
            print(f"[TRACK] Success: true")
            
            return result
            
        except Exception as e:
            end_time = datetime.now()
            latency_ms = int((end_time - start_time).total_seconds() * 1000)
            
            # Record failed call
            observer.record_call(
                call_id=call_id,
                tool_name=func_name,
                input_data=input_data,
                context_data=context_data,
                started_at=start_time,
                completed_at=end_time,
                error=e,
                latency_ms=latency_ms
            )
            
            print(f"[TRACK] Error: {json.dumps(str(e), indent=2)}")
            print(f"[TRACK] Duration: {latency_ms}ms")
            print(f"[TRACK] Success: false")
            
            raise e
    
    return async_wrapper


def create_sync_noauth_wrapper(observer, func, sig):
    """Create a sync wrapper for public/no-auth endpoints."""
    
    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        call_id = str(uuid.uuid4())
        start_time = datetime.now(timezone.utc)
        func_name = getattr(func, '__name__', 'unknown_function')
        
        input_data = {
            "args": list(args),
            "kwargs": dict(kwargs)
        }
        
        context_data = {
            "requires_auth": False,
            "function_signature": str(sig)
        }
        
        print(f"[TRACK] Function: {func_name} (no auth)")
        print(f"[TRACK] Call ID: {call_id}")
        print(f"[TRACK] Input: {json.dumps(input_data, indent=2, default=str)}")
        
        try:
            result = func(*args, **kwargs)
            end_time = datetime.now()
            latency_ms = int((end_time - start_time).total_seconds() * 1000)
            
            observer.record_call(
                call_id=call_id,
                tool_name=func_name,
                input_data=input_data,
                output_data=result,
                context_data=context_data,
                started_at=start_time,
                completed_at=end_time,
                latency_ms=latency_ms
            )
            
            print(f"[TRACK] Result: {json.dumps(str(result), indent=2)}")
            print(f"[TRACK] Duration: {latency_ms}ms")
            print(f"[TRACK] Success: true")
            
            return result
            
        except Exception as e:
            end_time = datetime.now()
            latency_ms = int((end_time - start_time).total_seconds() * 1000)
            
            observer.record_call(
                call_id=call_id,
                tool_name=func_name,
                input_data=input_data,
                context_data=context_data,
                started_at=start_time,
                completed_at=end_time,
                error=e,
                latency_ms=latency_ms
            )
            
            print(f"[TRACK] Error: {json.dumps(str(e), indent=2)}")
            print(f"[TRACK] Duration: {latency_ms}ms")
            print(f"[TRACK] Success: false")
            
            raise e
    
    return sync_wrapper