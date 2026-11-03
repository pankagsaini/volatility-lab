"""Background execution preserves inputs, saved reports and nonblocking status."""
from concurrent.futures import Future
from copy import deepcopy
from unittest import TestCase,main
from unittest.mock import Mock,patch
import pandas as pd
import backtest_jobs as jobs


class ControlledExecutor:
    """Queue work deterministically without threads, disk writes or network calls."""
    def __init__(self):self.tasks=[]

    def submit(self,fn,*args,**kwargs):
        future=Future();self.tasks.append((future,fn,args,kwargs));return future

    def finish(self,index=0):
        future,fn,args,kwargs=self.tasks[index]
        if future.done():raise AssertionError('Work must execute once')
        try:future.set_result(fn(*args,**kwargs))
        except BaseException as error:future.set_exception(error)
        return future


class ExecutionTests(TestCase):
    def settings(self):
        return {'name':'Test','symbol':'NIFTY','start':'2025-01-01','end':'2025-01-31',
                'legs':[{'Side':'Sell','Ratio':1}],'filters':[('IV',10,30)]}

    def result(self,settings):
        return pd.DataFrame([{'Net':10.}]),pd.DataFrame([{'Reason':'Missing candle'}]),settings,{'conflicts':2}

    def test_settings_are_deep_frozen_and_duplicate_jobs_stay_blocked_until_collected(self):
        executor=ControlledExecutor();state={};settings=self.settings()
        with patch.object(jobs,'_pool',executor),patch.object(jobs,'_run',return_value=(self.result(deepcopy(settings)),'run-1')) as run:
            self.assertTrue(jobs.submit(state,'Options',settings))
            settings['symbol']='BANKNIFTY';settings['legs'][0]['Ratio']=20;settings['filters'].append(('HV30',0,100))
            task=state[jobs.SLOT]
            self.assertEqual(task.settings,self.settings())
            self.assertTrue(jobs.pending(state));self.assertIsNone(jobs.collect(state))
            self.assertFalse(jobs.submit(state,'Futures',settings))
            self.assertEqual(len(executor.tasks),1)
            executor.finish()
            self.assertTrue(jobs.pending(state))
            self.assertFalse(jobs.submit(state,'Options',settings))
            self.assertEqual(jobs.collect(state),(True,'run-1'))
            self.assertFalse(jobs.pending(state));self.assertIsNone(jobs.collect(state))
            self.assertEqual(run.call_count,1)
            self.assertEqual(state['_backtest_completed_kind'],'Options')
            self.assertIn('builder_result',state)
            self.assertNotIn('futures_bt_v2',state)

    def test_run_saves_once_and_collection_never_saves_again(self):
        executor=ControlledExecutor();settings=self.settings();state={}
        result=self.result(settings)
        with patch.object(jobs,'_pool',executor),patch.object(jobs,'_options',return_value=result) as option_run,\
                patch('backtest_report.save_run',return_value='one-save') as save:
            jobs.submit(state,'Options',settings);executor.finish()
            self.assertEqual(state[jobs.SLOT].phase,'Saving results')
            self.assertEqual(jobs.collect(state),(True,'one-save'))
            self.assertIsNone(jobs.collect(state))
            save.assert_called_once_with(result[2],result[0],result[1],result[3])
            option_run.assert_called_once()

    def test_failed_or_unsaved_runs_preserve_both_previous_reports(self):
        for error in (ValueError('No valid candles'),OSError('Save failed'),RuntimeError('private response')):
            with self.subTest(error=type(error).__name__):
                executor=ControlledExecutor();old_options=object();old_futures=object()
                state={'builder_result':old_options,'futures_bt_v2':old_futures}
                with patch.object(jobs,'_pool',executor),patch.object(jobs,'_options',return_value=self.result(self.settings())),\
                        patch('backtest_report.save_run',side_effect=error):
                    jobs.submit(state,'Options',self.settings());executor.finish()
                    outcome=jobs.collect(state)
                self.assertFalse(outcome[0]);self.assertIs(state['builder_result'],old_options)
                self.assertIs(state['futures_bt_v2'],old_futures)
                self.assertNotIn('_backtest_completed_kind',state)
                self.assertFalse(jobs.pending(state))
                if isinstance(error,RuntimeError):self.assertNotIn('private',outcome[1])

    def test_futures_report_goes_to_its_own_slot(self):
        state={'builder_result':'previous options'};executor=ControlledExecutor()
        result=self.result(self.settings())
        with patch.object(jobs,'_pool',executor),patch.object(jobs,'_futures',return_value=result) as simulate,\
                patch.object(jobs,'_options') as wrong_engine,patch('backtest_report.save_run',return_value='future-run') as save:
            jobs.submit(state,'Futures',self.settings());executor.finish()
            self.assertEqual(jobs.collect(state),(True,'future-run'))
        simulate.assert_called_once();wrong_engine.assert_not_called();save.assert_called_once()
        self.assertEqual(state['builder_result'],'previous options')
        self.assertIs(state['futures_bt_v2'],result)
        self.assertEqual(state['_backtest_completed_kind'],'Futures')

    def test_unknown_market_never_enqueues_work(self):
        executor=ControlledExecutor()
        with patch.object(jobs,'_pool',executor),self.assertRaises(ValueError):
            jobs.submit({},'Unknown',self.settings())
        self.assertEqual(executor.tasks,[])


class EngineWiringTests(TestCase):
    def test_options_use_frozen_dates_confirmed_sessions_and_selected_signals(self):
        from upstox_data import INDICES
        settings={'name':'Test','symbol':'NIFTY','start':'2025-01-01','end':'2025-01-31','filters':[('IV',10,30)]}
        key=INDICES['NIFTY'];spot={'2025-01-03T09:20:00+05:30':(1,), '2025-01-02T09:20:00+05:30':(2,)}
        contracts=[{'instrument':'option'}];series={key:spot};signals=[('expiry','timestamp',{})]
        trades=pd.DataFrame([{'Net':12.}]);skips=pd.DataFrame([{'Reason':'Test skip'}])
        task=jobs.Task('Options',deepcopy(settings))
        with patch('backtest_engine.load',return_value=(contracts,series,3)) as load,\
                patch('strategy_backtest.simulate',return_value=(trades,skips)) as simulate,\
                patch('backtest_signals.load_signals',return_value=signals) as read_signals,\
                patch('archive_coverage.daily_sessions',return_value=['2025-01-02','2025-01-03']) as sessions,\
                patch('archive_coverage.session_report',return_value={'missing_sessions':1}) as coverage,\
                patch('collector_support.read_json',return_value={'status':'running'}):
            actual=jobs._options(task)
        load.assert_called_once_with('NIFTY','2025-01-01','2025-01-31')
        sessions.assert_called_once_with(key,'2025-01-01','2025-01-31')
        read_signals.assert_called_once_with('NIFTY')
        simulate.assert_called_once_with(contracts,series,key,task.settings,signals)
        coverage.assert_called_once_with(spot,'2025-01-01','2025-01-31',['2025-01-02','2025-01-03'])
        self.assertIs(actual[0],trades);self.assertIs(actual[1],skips)
        self.assertEqual(actual[3],{'conflicts':3,'underlying_first':min(spot),'underlying_last':max(spot),
                                  'underlying_observed_sessions':2,'download_state_at_run':'running','missing_sessions':1})
        self.assertNotIn('confirmed_sessions',settings)

    def test_options_without_filters_do_not_load_signal_archive(self):
        task=jobs.Task('Options',{'symbol':'BANKNIFTY','start':'2025-01-01','end':'2025-01-31','filters':[]})
        with patch('backtest_engine.load',return_value=([],{},0)),\
                patch('strategy_backtest.simulate',return_value=(pd.DataFrame(),pd.DataFrame())) as simulate,\
                patch('backtest_signals.load_signals') as signals,\
                patch('archive_coverage.daily_sessions',return_value=[]),\
                patch('archive_coverage.session_report',return_value={}),\
                patch('collector_support.read_json',return_value={}):
            actual=jobs._options(task)
        signals.assert_not_called();self.assertEqual(simulate.call_args.args[-1],[])
        self.assertIsNone(actual[3]['underlying_first']);self.assertIsNone(actual[3]['underlying_last'])
        self.assertEqual(actual[3]['underlying_observed_sessions'],0)

    def test_futures_pairs_directions_costs_and_unfinished_position_are_forwarded(self):
        bars={('2025-01-01','2025-02-27'):(100,101,99,100,10)}
        trades=pd.DataFrame([{'Net':1.}]);skips=pd.DataFrame([{'Reason':'Pending exit'}])
        position={'Status':'End of selected period','Unrealised gross':12.}
        for name,pair in [('Near - Far',(0,2)),('Near - Next',(0,1)),('Next - Far',(1,2))]:
            for direction,side in [('Buy near / sell far',1),('Sell near / buy far',-1)]:
                settings={'name':name+' carry','symbol':'BANKNIFTY','start':'2025-01-01','end':'2025-01-31',
                          'entry_carry':6.,'target_carry':6.66,'quantity':30,'direction':direction,
                          'slippage_bps':2.,'max_hold_days':20,'brokerage_per_order':25}
                with patch('futures_backtest.load',return_value=(bars,4)) as load,\
                        patch('futures_backtest.simulate',return_value=(trades,skips,position)) as simulate:
                    result=jobs._futures(jobs.Task('Futures',settings))
                load.assert_called_once_with('BANKNIFTY','2025-01-01','2025-01-31')
                simulate.assert_called_once_with(bars,pair,6.,6.66,30,side,2.,20,25)
                self.assertEqual(result[3],{'conflicts':4,'dated_contract_days':1,
                                           'open_position':True,'unfinished_position':position})


class InventoryTests(TestCase):
    def setUp(self):
        self.executor=ControlledExecutor();self.clock=[0.]
        self.state=patch.multiple(jobs,_fee_pool=self.executor,_inventory_future=None,_inventory_value=None,
                                  _inventory_attempt=float('-inf'),_inventory_error=None)
        self.state.start();self.time=patch.object(jobs,'monotonic',side_effect=lambda:self.clock[0]);self.time.start()

    def tearDown(self):self.time.stop();self.state.stop()

    def test_inventory_is_nonblocking_and_pending_requests_are_not_duplicated(self):
        saved={'generated_at':'now','summary':[],'contracts':[]}
        with patch('archive_coverage.inventory',return_value=saved) as inventory:
            self.assertEqual(jobs.inventory_status(),(None,True,None))
            inventory.assert_not_called()  # submission never executes on the rendering thread
            self.clock[0]=120
            self.assertEqual(jobs.inventory_status(),(None,True,None));self.assertEqual(len(self.executor.tasks),1)
            self.executor.finish()
            self.clock[0]=121
            value,busy,error=jobs.inventory_status()
            self.assertEqual(value,saved);self.assertIsNone(error)
            # A long first scan may immediately schedule the next permitted refresh.
            self.assertEqual(inventory.call_count,1)

    def test_failed_refresh_retains_last_good_inventory_and_obeys_cooldown(self):
        saved={'generated_at':'first','summary':[{'done':1}],'contracts':[]}
        with patch('archive_coverage.inventory',side_effect=[saved,RuntimeError('private path')]):
            jobs.inventory_status();self.executor.finish()
            self.clock[0]=1
            self.assertEqual(jobs.inventory_status(),(saved,False,None))
            self.clock[0]=59;jobs.inventory_status();self.assertEqual(len(self.executor.tasks),1)
            self.clock[0]=60;self.assertEqual(jobs.inventory_status(),(saved,True,None))
            self.executor.finish(1)
            self.clock[0]=61
            value,busy,error=jobs.inventory_status()
            self.assertEqual(value,saved);self.assertFalse(busy);self.assertIn('retained',error)
            self.assertNotIn('private',error)
            self.clock[0]=119;jobs.inventory_status();self.assertEqual(len(self.executor.tasks),2)


if __name__=='__main__':main()
