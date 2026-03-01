from fastapi import APIRouter, Request

from ..response import ok

router = APIRouter()


@router.get('/healthz')
def healthz(request: Request):
    return ok(request_id=request.state.request_id, data={'status': 'ok'})
