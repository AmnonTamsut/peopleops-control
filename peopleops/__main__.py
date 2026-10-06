"""Repeatable local and scheduled entrypoints."""
import argparse
import json
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description='PeopleOps Control — synthetic HR/payroll ETL')
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).resolve().parent.parent / 'data')
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run', help='Run a month and export its Excel evidence pack')
    run.add_argument('--period', required=True)
    run.add_argument('--scenario', choices=['clean', 'review', 'corrected'], default='clean')
    run.add_argument('--hr-url', help='Optional paginated HR endpoint (same contract as /mock/hr/employees)')
    run.add_argument('--output-dir', type=Path, default=Path('artifacts'))
    serve = commands.add_parser('serve', help='Start local control room')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8000)
    publish = commands.add_parser('publish-sqlserver', help='Publish a local run to a configured SQL Server')
    publish.add_argument('--run-id', required=True)
    args = parser.parse_args()
    if args.command == 'serve':
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(args.data_dir), host=args.host, port=args.port)
        return
    from .pipeline import PeopleOpsService
    service = PeopleOpsService(args.data_dir)
    if args.command == 'publish-sqlserver':
        from .mssql import publish_run
        evidence = service.get_run(args.run_id)
        if evidence is None:
            parser.error('Run not found')
        print(json.dumps(publish_run(evidence), indent=2))
        return
    from .connectors import fetch_employees
    from .reports import build_report
    employees = fetch_employees(args.hr_url, token=os.environ.get('PEOPLEOPS_HR_TOKEN')) if args.hr_url else None
    evidence = service.run(args.period, args.scenario, employees=employees)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    destination = args.output_dir / f'peopleops-{args.period}-{evidence["run"]["id"]}.xlsx'
    destination.write_bytes(build_report(evidence))
    print(json.dumps({'run': evidence['run'], 'metrics': evidence['metrics'], 'report': str(destination)}, indent=2))
    if evidence['run']['critical_count']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
