"""Explicit local engine lifecycle action; never a retain/recall success claim."""
import re
from .. import errors
from ..api.request import Response
from .engine_control import get_controller
from .api import _body

SCOPE='memory:engine:manage'

def controller(core):
    path=next(row[2] for row in core.conn.execute('PRAGMA database_list') if row[1]=='main')
    return get_controller(path,core.schema_version)

def snapshot(core,principal):
    manager=controller(core);value=manager.snapshot()
    value['can_start']=SCOPE in principal.scopes and core.actor_kind(principal.actor_id) in ('human','system')
    return value

def get_engine(request,core,principal,match):
    return Response(200,snapshot(core,principal))

def start_engine(request,core,principal,match):
    if core.actor_kind(principal.actor_id) not in ('human','system'):
        raise errors.ForbiddenActorKind('only a human or system operator may start the Memory engine')
    if request.query:raise errors.InvalidRequest('engine startup accepts no query parameters')
    _body(request,set())
    if controller(core).profile is None:raise errors.InvalidRequest('local engine startup is not configured',reason='engine_control_unavailable')
    controller(core).start()
    return Response(202,snapshot(core,principal))

def routes(route_cls):
    return (route_cls('GET',re.compile(r'^/v1/memory/engine$'),get_engine,'memory:read'),
            route_cls('POST',re.compile(r'^/v1/memory/engine/start$'),start_engine,SCOPE))
