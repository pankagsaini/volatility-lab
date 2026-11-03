"""Backtest navigation must not scan archives or wait for a simulation."""
from concurrent.futures import Future
from contextlib import ExitStack
from unittest import TestCase,main
from unittest.mock import patch
import pandas as pd
from streamlit.testing.v1 import AppTest
import backtest_ui as ui
import backtest_jobs as jobs

SOURCE='import backtest_ui; backtest_ui.render()'


class BacktestUITests(TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(ui,'checked_charges',return_value={'checked':'test'}))
        self.inventory=self.stack.enter_context(patch.object(jobs,'inventory_status',return_value=(None,True,None)))
        self.future=Future()
        self.submit=self.stack.enter_context(patch.object(jobs._pool,'submit',return_value=self.future))

    def app(self):
        app=AppTest.from_string(SOURCE,default_timeout=10).run()
        self.clean(app);return app

    def clean(self,app):self.assertFalse(app.exception,[x.message for x in app.exception])

    def button(self,app,label):return next(x for x in app.button if x.label==label)

    def test_closed_panels_skip_inventory_and_saved_results(self):
        with patch.object(ui,'saved_run_summary') as summary:
            app=self.app();app.run();self.clean(app)
            self.inventory.assert_not_called();summary.assert_not_called()
            self.submit.assert_not_called()

    def test_open_archive_loads_in_background_only_when_requested(self):
        app=self.app()
        app.session_state['backtest_archive_details']=True
        app.run();self.clean(app)
        self.inventory.assert_called_once()
        self.assertTrue(any('background' in x.value for x in app.caption))
        app.session_state['backtest_archive_details']=False
        app.run();self.clean(app)
        self.inventory.assert_called_once()

    def test_options_run_freezes_settings_keeps_report_then_loads_result(self):
        app=self.app()
        previous=(pd.DataFrame(),pd.DataFrame(),{'name':'Previous run','symbol':'NIFTY','start':'2024-10-01','end':'2024-10-31','engine':'multi_leg_v1'}, {})
        app.session_state['builder_result']=previous
        self.button(app,'Run backtest').click().run();self.clean(app)
        task=app.session_state[jobs.SLOT]
        self.assertEqual(task.kind,'Options')
        self.assertTrue(any('Previous run' in x.value for x in app.caption))
        app.selectbox(key='builder_symbol').set_value('BANKNIFTY').run();self.clean(app)
        self.assertEqual(task.settings['symbol'],'NIFTY')
        self.assertTrue(self.button(app,'Run backtest').disabled)
        self.submit.assert_called_once()
        result=(pd.DataFrame(),pd.DataFrame(),task.settings,{'download_state_at_run':'finished_available_requests'})
        self.future.set_result((result,'saved-test-run'))
        app.run();self.button(app,'View results').click().run();self.clean(app)
        self.assertNotIn(jobs.SLOT,app.session_state)
        self.assertEqual(app.session_state['builder_result'][2]['symbol'],'NIFTY')
        self.assertFalse(self.button(app,'Run backtest').disabled)

    def test_futures_completion_can_be_opened_after_switching_market(self):
        app=self.app();app.session_state['backtest_market']='Futures';app.run();self.clean(app)
        self.button(app,'Run futures test').click().run();self.clean(app)
        task=app.session_state[jobs.SLOT]
        self.assertEqual(task.kind,'Futures')
        app.session_state['backtest_market']='Options';app.run();self.clean(app)
        self.assertTrue(self.button(app,'Run backtest').disabled)
        self.future.set_result(((pd.DataFrame(),pd.DataFrame(),task.settings,{'open_position':False}),'saved-futures-run'))
        app.run();self.button(app,'View results').click().run();self.clean(app)
        self.assertEqual(app.session_state['backtest_market'],'Futures')
        self.assertEqual(app.session_state['futures_bt_v2'][2]['engine'],'dated_futures_v1')
        self.assertFalse(self.button(app,'Run futures test').disabled)

    def test_failed_run_retains_previous_report_and_allows_retry(self):
        app=self.app()
        previous=(pd.DataFrame(),pd.DataFrame(),{'name':'Previous run','symbol':'NIFTY','start':'2024-10-01','end':'2024-10-31','engine':'multi_leg_v1'}, {})
        app.session_state['builder_result']=previous
        self.button(app,'Run backtest').click().run();self.clean(app)
        self.future.set_exception(ValueError('Archive not readable'))
        app.run();self.button(app,'View results').click().run();self.clean(app)
        self.assertTrue(any('Archive not readable' in x.value for x in app.error))
        self.assertEqual(app.session_state['builder_result'][2]['name'],'Previous run')
        self.assertFalse(self.button(app,'Run backtest').disabled)


if __name__=='__main__':main()
