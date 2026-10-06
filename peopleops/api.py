"""Local portfolio demo API. Uses synthetic sources only."""
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .fixtures import get_employees
from .pipeline import PeopleOpsService
from .reports import build_report

ROOT = Path(__file__).resolve().parent.parent


class RunRequest(BaseModel):
    period: str = Field(default='2026-09', pattern=r'^\d{4}-(0[1-9]|1[0-2])$')
    scenario: Literal['clean', 'review', 'corrected'] = 'review'


def create_app(data_dir: Path | None = None, seed: bool = True) -> FastAPI:
    service = PeopleOpsService(data_dir or Path(os.environ.get('PEOPLEOPS_DATA_DIR', ROOT / 'data')))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if seed and not service.dashboard().get('run'):
            for period in ('2026-07', '2026-08', '2026-09'):
                service.run(period, 'clean')
            service.run('2026-09', 'review')
        yield

    app = FastAPI(title='PeopleOps Control', version='1.0.0', lifespan=lifespan,
                  description='Synthetic HR/payroll reconciliation portfolio. Local demo, no real employee data.')
    app.state.service = service

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'storage': 'sqlite', 'dataset': 'synthetic', 'mode': 'local-demo'}

    @app.get('/api/dashboard')
    def dashboard(period: str | None = Query(default=None, pattern=r'^\d{4}-(0[1-9]|1[0-2])$')):
        try:
            return service.dashboard(period)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None

    @app.post('/api/runs')
    def run_pipeline(request: RunRequest):
        try:
            return service.run(request.period, request.scenario)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        except (RuntimeError, OSError):
            raise HTTPException(status_code=503, detail='Pipeline could not finish. Retry after checking local storage and the run log.') from None

    @app.get('/api/runs/{run_id}')
    def run_detail(run_id: str):
        evidence = service.get_run(run_id)
        if evidence is None:
            raise HTTPException(status_code=404, detail='Run not found')
        return evidence

    @app.get('/api/runs/{run_id}/report')
    def report(run_id: str):
        evidence = run_detail(run_id)
        payload = build_report(evidence)
        # Run IDs originate from the pipeline, never interpolated into filesystem paths.
        return Response(payload, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition': f'attachment; filename="peopleops-{evidence["run"]["period"]}-{evidence["run"]["id"]}.xlsx"'})

    @app.get('/mock/hr/employees')
    def mock_hr(page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100)):
        employees = get_employees()
        start = (page - 1) * page_size
        return {'items': employees[start:start + page_size], 'has_more': start + page_size < len(employees)}

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'static' / 'index.html')

    app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
    return app


app = create_app()
