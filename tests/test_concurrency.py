"""Concurrent callers share one immutable run through the public service."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from peopleops.pipeline import PeopleOpsService


class ConcurrentRunTests(unittest.TestCase):
    def test_simultaneous_identical_imports_create_one_published_run(self) -> None:
        with TemporaryDirectory() as directory:
            services = [PeopleOpsService(Path(directory)), PeopleOpsService(Path(directory))]
            start = Barrier(2)

            def run(service: PeopleOpsService) -> dict:
                start.wait(timeout=5)
                return service.run('2026-09', 'clean')

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(run, service) for service in services]
                results = [future.result(timeout=10) for future in futures]

            self.assertEqual(results[0]['run']['id'], results[1]['run']['id'])
            self.assertEqual(sorted(result['run']['reused'] for result in results), [False, True])
            dashboard = services[0].dashboard('2026-09')
            self.assertEqual(len(dashboard['runs']), 1)
            self.assertEqual(dashboard['metrics']['headcount'], 72)
            self.assertEqual(dashboard['metrics']['published_run_id'], results[0]['run']['id'])


if __name__ == '__main__':
    unittest.main()
