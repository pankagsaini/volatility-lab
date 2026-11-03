"""Lazy report sections retain P&L, costs, ledgers and unfinished-position disclosure."""
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase,main
from unittest.mock import patch
import pandas as pd
from pandas.testing import assert_frame_equal
from streamlit.testing.v1 import AppTest
import backtest_report as report


SOURCE='''import json
import pandas as pd
import streamlit as st
from backtest_report import render
settings={'name':'Test report','symbol':'NIFTY','start':'2026-09-30','end':'2026-10-02','engine':'multi_leg_v1'}
trades=pd.DataFrame({'Date':['2026-09-30','2026-10-01','2026-10-02'],
 'Exit reason':['Scheduled exit']*3,'Net':[100.,-50.,25.],'Gross':[115.,-35.,40.],
 'Charges':[10.]*3,'Slippage':[5.]*3,'Capital required':[1000.]*3,
 'Legs':[json.dumps([{'Contract':'TEST'+str(i),'Quantity':75,'Entry':100.,'Exit':101.}]) for i in range(3)]})
skips=pd.DataFrame([{'Date':'2026-10-03','Reason':'Missing entry candle'}])
coverage={'conflicts':2,'download_state_at_run':'finished_available_requests',
 'sessions':[{'Date':'2026-10-03','Status':'Missing minutes; daily candle confirms session'}]}
render(trades,skips,settings,coverage)
'''


class ReportTests(TestCase):
    def app(self,source=SOURCE):
        app=AppTest.from_string(source,default_timeout=15).run()
        self.assertFalse(app.exception,[item.message for item in app.exception])
        return app

    def tab(self,app,label,engine='multi_leg_v1'):
        app.session_state['backtest_report_tab_'+engine]=label
        app.run()
        self.assertFalse(app.exception,[item.message for item in app.exception])
        return app

    def metrics(self,app):return {m.label:m.value for m in app.metric}

    def test_default_performance_keeps_metrics_without_building_hidden_tables(self):
        app=self.app()
        metrics=self.metrics(app)
        self.assertEqual(metrics['Net P&L'],'₹75')
        self.assertEqual(metrics['Closed-trade drawdown'],'₹50')
        self.assertEqual(metrics['Win rate'],'66.7%')
        self.assertEqual(metrics['Profit factor'],'2.50')
        self.assertEqual(metrics['Total trading costs'],'₹45')
        self.assertEqual(metrics['Completed trades'],'3')
        self.assertEqual(len(app.get('plotly_chart')),1)
        self.assertEqual(len(app.dataframe),0)
        self.assertEqual(len(app.selectbox),0)

    def test_monthly_and_weekday_only_render_selected_aggregations(self):
        app=self.tab(self.app(),'Monthly & weekday')
        self.assertEqual(len(app.get('plotly_chart')),0)
        self.assertEqual(len(app.dataframe),2)
        monthly=app.dataframe[0].value
        self.assertEqual(monthly.loc['2026-09','Net INR'],100.)
        self.assertEqual(monthly.loc['2026-10','Net INR'],-25.)
        self.assertEqual(monthly.Trades.sum(),3)
        self.assertEqual(app.dataframe[1].value['Net INR'].sum(),75.)
        self.assertEqual(self.metrics(app)['Net P&L'],'₹75')

    def test_ledger_export_is_deferred_and_trade_inspection_still_works(self):
        captured=[]
        def download(*args,**kwargs):captured.append((args,kwargs));return False
        with patch.object(report.st,'download_button',side_effect=download):
            app=self.app()
            self.assertEqual(captured,[])
            self.tab(app,'Trade ledger')
            self.assertEqual(len(captured),1)
            args,kwargs=captured[0]
            self.assertTrue(callable(args[1]))
            self.assertEqual(kwargs['on_click'],'ignore')
            exported=pd.read_csv(StringIO(args[1]().decode()))
            self.assertEqual(exported['Total costs'].sum(),45.)
            self.assertEqual(exported.Net.sum(),75.)
            self.assertEqual(list(exported.columns),list(app.dataframe[0].value.columns))
            self.assertEqual(app.dataframe[1].value.Contract.iloc[0],'TEST0')
            # AppTest does not yet forward native tab widget state on interaction.
            app.selectbox[0].set_value(1)
            self.tab(app,'Trade ledger')
            self.assertEqual(app.dataframe[1].value.Contract.iloc[0],'TEST1')
            self.assertEqual(len(app.get('plotly_chart')),0)

    def test_coverage_keeps_exclusions_without_constructing_plot_or_ledger(self):
        app=self.tab(self.app(),'Data coverage')
        self.assertEqual(len(app.dataframe),2)
        self.assertEqual(app.dataframe[1].value.Reason.iloc[0],'Missing entry candle')
        self.assertEqual(len(app.get('plotly_chart')),0)
        self.assertEqual(len(app.selectbox),0)
        self.assertEqual(self.metrics(app)['Net P&L'],'₹75')

    def test_unfinished_futures_position_remains_visible_on_every_tab(self):
        source=SOURCE.replace("'engine':'multi_leg_v1'","'engine':'dated_futures_v1'")
        source=source.replace('render(trades,skips,settings,coverage)',
            "coverage['unfinished_position']={'Status':'Open','Unrealised gross':123.}\nrender(trades,skips,settings,coverage)")
        app=self.app(source)
        for label in ('Performance','Monthly & weekday','Trade ledger','Data coverage'):
            self.tab(app,label,'dated_futures_v1')
            self.assertTrue(any('excluded from realised P&L' in x.value for x in app.warning))
            self.assertEqual(app.dataframe[0].value['Unrealised gross'].iloc[0],123.)
            self.assertEqual(self.metrics(app)['Net P&L'],'₹75')

    def test_empty_trades_keep_open_position_and_exclusion_records(self):
        source=SOURCE.replace('render(trades,skips,settings,coverage)',
            "coverage['unfinished_position']={'Status':'Open','Unrealised gross':123.}\nrender(pd.DataFrame(),skips,settings,coverage)")
        app=self.app(source)
        self.assertEqual(len(app.metric),0)
        self.assertEqual(len(app.dataframe),3)
        self.assertTrue(any('No complete trades' in x.value for x in app.info))


class SavedRunTests(TestCase):
    def test_atomic_save_preserves_schema_and_does_not_change_frames(self):
        with TemporaryDirectory() as folder,patch.object(report,'STORE',Path(folder)):
            trades=pd.DataFrame([{'Net':100.,'Charges':10.,'Slippage':5.}])
            skips=pd.DataFrame([{'Date':'2026-10-01','Reason':'Missing candle'}])
            expected=trades.copy(deep=True)
            name=report.save_run({'symbol':'NIFTY'},trades,skips,{'conflicts':2})
            files=list(Path(folder).iterdir())
            self.assertEqual([p.name for p in files],[name+'.json'])
            record=json.loads(files[0].read_text(encoding='utf-8'))
            self.assertEqual(set(record),{'settings','coverage','trades','skips'})
            self.assertEqual(record['trades'][0],{'Net':100.,'Charges':10.,'Slippage':5.})
            self.assertEqual(record['skips'][0]['Reason'],'Missing candle')
            assert_frame_equal(trades,expected)


if __name__=='__main__':main()
