"""Bounded single-writer handoff for overlapping Render deployments.

/health is a liveness handoff signal while an OLD writer holds the lock.
/ready and every business request remain 503 until this process owns the lock.
No session is killed and only the explicit lock-busy exception is retried.
"""
import asyncio
from contextlib import asynccontextmanager
import logging
import math
import time

from fastapi import FastAPI
from starlette.responses import JSONResponse

from .postgres_store import WriterBusyError
from .production_factory import create_production_app, validate_production_config

log = logging.getLogger(__name__)


def create_rolling_production_app(*, environ=None, provider=None, factory=None,
                                  wait_seconds=None, retry_seconds=2.0):
    env, *_ = validate_production_config(environ)
    if wait_seconds is None:
        try:wait_seconds=float(env.get('HR_STARTUP_HANDOFF_SECONDS','180'))
        except ValueError:raise RuntimeError('HR_STARTUP_HANDOFF_SECONDS must be between 1 and 600') from None
        if not math.isfinite(wait_seconds) or not 1<=wait_seconds<=600:
            raise RuntimeError('HR_STARTUP_HANDOFF_SECONDS must be between 1 and 600')
    build = factory or (lambda: create_production_app(environ=env,provider=provider))
    state = {'phase':'initializing','app':None,'code':None}

    @asynccontextmanager
    async def lifespan(app):
        stop=asyncio.Event()
        child_context=None
        async def load():
            nonlocal child_context
            deadline=time.monotonic()+wait_seconds
            while not stop.is_set():
                try:
                    # Do not block the event loop: health can respond during handoff.
                    child=await asyncio.to_thread(build)
                    child_context=child.router.lifespan_context(child)
                    await child_context.__aenter__()
                    if stop.is_set():return
                    state.update(phase='ready',app=child,code=None)
                    return
                except WriterBusyError:
                    if time.monotonic()>=deadline:
                        state.update(phase='failed',code='writer_handoff_timeout')
                        log.error('Production startup failed: writer_handoff_timeout')
                        return
                    state.update(phase='waiting_for_writer',code=None)
                    try:await asyncio.wait_for(stop.wait(),timeout=min(retry_seconds,max(0,deadline-time.monotonic())))
                    except asyncio.TimeoutError:pass
                except Exception:
                    # Exception text may contain upstream DSNs/credentials: never log it.
                    state.update(phase='failed',code='production_initialization_failed')
                    log.error('Production startup failed: production_initialization_failed')
                    return
        task=asyncio.create_task(load())
        try:yield
        finally:
            stop.set()
            # Do not abandon a constructor thread that may acquire the writer lock.
            await task
            state.update(phase='stopped',app=None)
            if child_context is not None:await child_context.__aexit__(None,None,None)

    app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.startup=state

    def ready():
        if state['phase']!='ready':return False
        store=getattr(state['app'].state,'store',None)
        if getattr(store,'is_remote',False) and (store.db is None or store.db.closed):
            state.update(phase='failed',code='storage_connection_lost')
            return False
        return True

    @app.api_route('/health',methods=['GET','HEAD'])
    async def health():
        is_ready=ready()
        phase=state['phase']
        # Only confirmed lock contention allows temporary healthy handoff.
        healthy=is_ready or phase=='waiting_for_writer'
        return JSONResponse({'status':'ok' if is_ready else phase,'ready':is_ready},
                            status_code=200 if healthy else 503,headers={'Cache-Control':'no-store'})

    @app.api_route('/ready',methods=['GET','HEAD'])
    async def readiness():
        is_ready=ready()
        return JSONResponse({'ready':is_ready,'status':state['phase']},
                            status_code=200 if is_ready else 503,headers={'Cache-Control':'no-store'})

    class Dispatch:
        async def __call__(self,scope,receive,send):
            if scope['type']=='http':
                if ready():return await state['app'](scope,receive,send)
                response=JSONResponse({'error':{'code':state['code'] or 'service_starting',
                    'message':'服务正在等待旧实例完成交接，请稍后重试。' if state['phase']=='waiting_for_writer'
                    else '服务尚未就绪，请稍后重试；若持续失败，请检查部署状态。'}},
                    status_code=503,headers={'Retry-After':'3','Cache-Control':'no-store'})
                return await response(scope,receive,send)
            if scope['type']=='websocket':await send({'type':'websocket.close','code':1013})
    app.mount('/',Dispatch())
    return app
