import sys
import tempfile
import threading
import unittest
import json
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Store, AppError, handler_for, backup_database, ThreadingHTTPServer


class ProcurementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'data.sqlite3'
        self.store = Store(self.path)
        self.purchase = dict(name='Пошив спецодежды', type='work', quantity='1000', unit='шт.',
                             due='2026-12-10', order='024', requirements='Из ткани заказчика')
        self.offer = dict(company='Учебная компания', price='258', delivery='0', extra='0',
                          ready='2026-12-05', days='3', payment='50%', match='yes', comment='')

    def tearDown(self):
        self.temp.cleanup()

    def populated(self):
        p = self.store.save_purchase(self.purchase)
        return self.store.save_offer(p['id'], self.offer)

    def decision(self, p, **changes):
        return dict(offerId=p['offers'][0]['id'], reason='Соответствует ТЗ и срокам',
                    purchaseRevision=p['revision'], offerRevision=p['offers'][0]['revision'], **changes)

    def test_complete_flow_and_restart(self):
        p = self.populated()
        self.assertEqual(p['offers'][0]['total_cents'], 25800000)
        self.assertEqual(p['offers'][0]['arrival'], '2026-12-08')
        self.assertEqual(p['offers'][0]['risks'], [])
        self.store.decide(p['id'], self.decision(p))
        restored = Store(self.path).detail(p['id'])
        self.assertEqual(restored['decision']['offerId'], p['offers'][0]['id'])
        self.assertFalse(restored['decision']['needsReview'])

    def test_unknown_cost_is_not_zero(self):
        self.offer['delivery'] = ''
        self.offer['days'] = ''
        p = self.populated()
        o = p['offers'][0]
        self.assertIsNone(o['total_cents'])
        self.assertIsNone(o['arrival'])
        self.assertEqual(len(o['risks']), 2)
        with self.assertRaises(AppError):
            self.store.decide(p['id'], self.decision(p))
        self.store.decide(p['id'], self.decision(p, acceptRisks=True))

    def test_late_and_deviation_risks(self):
        self.offer.update(ready='2026-12-10', match='deviation')
        p = self.populated()
        self.assertEqual(len(p['offers'][0]['risks']), 2)

    def test_fractional_quantity_and_rounding(self):
        self.purchase['quantity'] = '1.005'
        self.offer.update(price='1', delivery='0.01', extra='0.02')
        p = self.populated()
        self.assertEqual(p['offers'][0]['total_cents'], 104)

    def test_changed_offer_invalidates_decision(self):
        p = self.populated()
        self.store.decide(p['id'], self.decision(p))
        changed = self.store.save_offer(p['id'], dict(self.offer, price='259', revision=1), p['offers'][0]['id'])
        self.assertTrue(changed['decision']['needsReview'])
        self.store.decide(p['id'], self.decision(changed))
        self.assertFalse(self.store.detail(p['id'])['decision']['needsReview'])

    def test_changed_requirements_invalidates_decision(self):
        p = self.populated()
        self.store.decide(p['id'], self.decision(p))
        changed = self.store.save_purchase(dict(self.purchase, requirements='Новые размеры', revision=1), p['id'])
        self.assertTrue(changed['decision']['needsReview'])

    def test_stale_updates_rejected(self):
        p = self.populated()
        self.store.save_purchase(dict(self.purchase, revision=1), p['id'])
        with self.assertRaises(AppError) as e:
            self.store.save_purchase(dict(self.purchase, revision=1), p['id'])
        self.assertEqual(e.exception.status, 409)
        with self.assertRaises(AppError):
            self.store.decide(p['id'], self.decision(p))

    def test_cannot_choose_offer_from_other_purchase(self):
        p = self.populated()
        other = self.store.save_purchase(self.purchase)
        with self.assertRaises(AppError):
            self.store.decide(other['id'], self.decision(p))

    def test_validation(self):
        for quantity in ['0', '-1', 'NaN', '1.0001', '1000001']:
            with self.subTest(quantity=quantity), self.assertRaises(AppError):
                self.store.save_purchase(dict(self.purchase, quantity=quantity))
        with self.assertRaises(AppError):
            self.store.save_purchase(dict(self.purchase, due='2026-02-30'))
        p = self.store.save_purchase(self.purchase)
        for price in ['-1', 'NaN', '1.001']:
            with self.subTest(price=price), self.assertRaises(AppError):
                self.store.save_offer(p['id'], dict(self.offer, price=price))

    def test_demo_is_idempotent(self):
        self.assertTrue(self.store.demo())
        self.assertFalse(self.store.demo())
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(len(self.store.list()[0]['offers']), 3)

    def test_backup(self):
        p = self.populated()
        target = Path(self.temp.name) / 'backup.sqlite3'
        backup_database(self.path, target)
        self.assertEqual(Store(target).detail(p['id'])['name'], p['name'])
        with self.assertRaises(RuntimeError):
            backup_database(self.path, target)

    def test_http_api_and_security(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.store))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base + '/') as response:
                self.assertIn(b'app.js', response.read())
            body = json.dumps(self.purchase).encode()
            with urlopen(Request(base + '/api/purchases', data=body,
                                headers={'Content-Type': 'application/json'})) as response:
                self.assertEqual(response.status, 201)
            with urlopen(base + '/api/purchases') as response:
                self.assertEqual(len(json.load(response)), 1)
            for route, headers, status in [('/app.py', {}, 404),
                                            ('/api/purchases', {'Origin': 'https://example.org'}, 403),
                                            ('/api/purchases', {'Host': 'example.org'}, 403)]:
                with self.subTest(route=route, headers=headers), self.assertRaises(HTTPError) as e:
                    urlopen(Request(base + route, headers=headers))
                self.assertEqual(e.exception.code, status)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
