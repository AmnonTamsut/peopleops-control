"""HR extraction behavior at the external HTTP boundary, without live servers."""
import unittest

import httpx

from peopleops.connectors import ExtractionError, fetch_employees
from peopleops.fixtures import get_employees


class HRExtractionTests(unittest.TestCase):
    def test_transient_failure_retries_then_extracts_every_page(self) -> None:
        rows = get_employees()
        requested_pages = []

        def source(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params['page'])
            requested_pages.append(page)
            if requested_pages == [1]:
                return httpx.Response(503)
            return httpx.Response(200, json={
                'items': rows[(page - 1) * 25:page * 25], 'has_more': page < 3,
            })

        with httpx.Client(transport=httpx.MockTransport(source)) as client:
            employees = fetch_employees('https://hr.example/employees', client=client)
        self.assertEqual(requested_pages, [1, 1, 2, 3])
        self.assertEqual(len(employees), 72)
        self.assertEqual(employees[-1]['employee_id'], 'EMP-072')

    def test_transient_failure_stops_at_retry_limit(self) -> None:
        requests = []

        def source(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(429)

        with httpx.Client(transport=httpx.MockTransport(source)) as client:
            with self.assertRaisesRegex(ExtractionError, 'bounded retries'):
                fetch_employees('https://hr.example/employees', client=client, retries=1)
        self.assertEqual(len(requests), 2)

    def test_authentication_error_fails_without_retry_or_disclosing_token(self) -> None:
        requests = []
        token = 'synthetic-test-secret'

        def source(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(401, text=token)

        with httpx.Client(transport=httpx.MockTransport(source)) as client:
            with self.assertRaises(ExtractionError) as raised:
                fetch_employees('https://hr.example/employees', client=client, token=token)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].headers['Authorization'], f'Bearer {token}')
        self.assertNotIn(token, str(raised.exception))

    def test_transport_errors_are_bounded_and_do_not_expose_credential_url(self) -> None:
        attempts = []
        sensitive_url = 'https://hr.example/employees?token=synthetic-test-secret'

        def source(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            raise httpx.ConnectError(sensitive_url, request=request)

        with httpx.Client(transport=httpx.MockTransport(source)) as client:
            with self.assertRaises(ExtractionError) as raised:
                fetch_employees(sensitive_url, client=client, retries=0)
        self.assertEqual(len(attempts), 1)
        self.assertNotIn('synthetic-test-secret', str(raised.exception))

    def test_malformed_pages_fail_without_returning_partial_snapshot(self) -> None:
        malformed_pages = [[], {'items': [], 'has_more': 'false'},
                           {'items': [], 'has_more': True}, {'has_more': False}]
        for page in malformed_pages:
            with self.subTest(page=page):
                with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=page))) as client:
                    with self.assertRaises(ExtractionError):
                        fetch_employees('https://hr.example/employees', client=client)

    def test_invalid_json_is_reported_as_safe_extraction_failure(self) -> None:
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text='not JSON'))) as client:
            with self.assertRaisesRegex(ExtractionError, 'invalid JSON'):
                fetch_employees('https://hr.example/employees', client=client)

    def test_employee_contract_rejects_ambiguous_values(self) -> None:
        invalid_fields = [('employee_id', ' '), ('employee_name', ''), ('department', None),
                          ('base_salary_cents', True), ('hourly_cents', 3.5), ('active', 1)]
        for field, value in invalid_fields:
            with self.subTest(field=field):
                item = dict(get_employees()[0], **{field: value})
                with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'items': [item], 'has_more': False}))) as client:
                    with self.assertRaisesRegex(ExtractionError, 'employee contract'):
                        fetch_employees('https://hr.example/employees', client=client)

    def test_repeated_id_on_later_page_rejects_entire_extraction(self) -> None:
        requests = []

        def source(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params['page'])
            requests.append(page)
            return httpx.Response(200, json={'items': [get_employees()[0]], 'has_more': page == 1})

        with httpx.Client(transport=httpx.MockTransport(source)) as client:
            with self.assertRaisesRegex(ExtractionError, 'repeated employee IDs'):
                fetch_employees('https://hr.example/employees', client=client)
        self.assertEqual(requests, [1, 2])

    def test_page_limit_prevents_unbounded_extraction(self) -> None:
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'items': [get_employees()[0]], 'has_more': True}))) as client:
            with self.assertRaisesRegex(ExtractionError, 'pagination exceeded'):
                fetch_employees('https://hr.example/employees', client=client, max_pages=1)


if __name__ == '__main__':
    unittest.main()
