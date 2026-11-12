import copy
import datetime
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
def module(name):
    spec=importlib.util.spec_from_file_location(name, ROOT/(name+'.py'))
    value=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value
p=module('remote_probe')
s=module('server')

class AssociationTests(unittest.TestCase):
    def test_distributed_workers_and_logger_share_one_task(self):
        procs={10:dict(pid=10,ppid=1,exe='/bin/bash'),
               20:dict(pid=20,ppid=10,exe='/env/bin/python'),
               21:dict(pid=21,ppid=20,exe='/env/bin/python'),
               22:dict(pid=22,ppid=20,exe='/env/bin/python'),
               23:dict(pid=23,ppid=21,exe='/env/bin/python'),
               30:dict(pid=30,ppid=10,exe='/env/bin/python')}
        self.assertEqual([p.family_root(i,procs) for i in [20,21,22,23]], [20]*4)
        self.assertEqual(p.family_root(30,procs),30)

    def fixture(self, directory, rid='abc123', pid=42, hostname='container-one', ago=0):
        stamp=datetime.datetime.fromtimestamp(time.time()-ago).strftime('%Y%m%d_%H%M%S')
        folder=Path(directory)/('run-'+stamp+'-'+rid)
        (folder/'files').mkdir(parents=True)
        (folder/'files/swanlab-metadata.json').write_text(json.dumps({'runtime':{'pid':pid,'hostname':hostname,'cwd':'/training'}}))
        return folder

    def member(self):
        return dict(pid=42,ppid=1,start=time.time()-20,cwd='/training',exe='/env/python',argv=['/env/python','train.py'])

    def test_exact_metadata_and_binary_run_id_match(self):
        with tempfile.TemporaryDirectory() as temp:
            self.fixture(temp)
            response=subprocess.CompletedProcess([],0,stdout='__SWAN_HEADER__'+json.dumps(dict(project='demo',workspace='me',name='training-a',run_id='abc123')),stderr='')
            with patch.object(p,'candidate_dirs',return_value={Path(temp)}),patch.object(p.subprocess,'run',return_value=response):
                result=p.experiment_for([self.member()],'container-one')
            self.assertEqual(result[0]['name'],'training-a')
            self.assertEqual(result[0]['url'],'https://swanlab.cn/@me/demo/runs/abc123/chart')

    def test_reused_pid_wrong_host_and_unrelated_process_rejected(self):
        for kwargs in [dict(ago=1000),dict(hostname='other'),dict(pid=999)]:
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as temp:
                self.fixture(temp,**kwargs)
                with patch.object(p,'candidate_dirs',return_value={Path(temp)}),patch.object(p.subprocess,'run') as process:
                    self.assertEqual(p.experiment_for([self.member()],'container-one'),[])
                    process.assert_not_called()

    def test_ambiguous_runs_are_not_guessed(self):
        with tempfile.TemporaryDirectory() as temp:
            self.fixture(temp,'a');self.fixture(temp,'b')
            with patch.object(p,'candidate_dirs',return_value={Path(temp)}):
                self.assertEqual(p.experiment_for([self.member()],'container-one'),[])

    def test_live_process_counts_as_busy_even_at_zero_utilization(self):
        gpu='0, uuid-a, RTX 3090, 24576, 100, 24476, 0\n1, uuid-b, RTX 3090, 24576, 1, 24575, 0\n2, uuid-c, RTX 3090, 24576, N/A, N/A, N/A\n'
        responses=[subprocess.CompletedProcess([],0,gpu,''),subprocess.CompletedProcess([],0,'uuid-a, 42, 90\n','')]
        with patch.object(p,'run',side_effect=responses),patch.object(p,'read_processes',return_value={}),patch.object(p,'experiment_for',return_value=[]):
            result=p.collect()
        self.assertEqual([g['state'] for g in result['gpus']],['busy','idle','unknown'])
        self.assertEqual(result['tasks'][0]['process_visible'],False)

class DashboardTests(unittest.TestCase):
    def test_region_settings_validate_references_and_duplicates(self):
        settings=copy.deepcopy(s.DEFAULT)
        settings['instances']={'1':{'name':'训练服务器','region':'nmb2'}}
        self.assertEqual(s.validate_settings(settings),settings)
        settings['instances']['1']['region']='missing'
        with self.assertRaises(ValueError):s.validate_settings(settings)
        settings=copy.deepcopy(s.DEFAULT);settings['regions'].append(settings['regions'][0])
        with self.assertRaises(ValueError):s.validate_settings(settings)

    def test_stale_telemetry_is_marked_and_settings_do_not_modify_registry(self):
        m=s.Monitor()
        m.samples={'1':dict(online=True,checked_at=time.time()-60,gpus=[],tasks=[],error='')}
        registry={'instances':[dict(id=1,name='nmb2-1',region='nmb2',host='example',port=1,user='root')]}
        original=copy.deepcopy(registry)
        settings=copy.deepcopy(s.DEFAULT);settings['instances']={'1':dict(name='我的实例',region='cqa1')}
        with patch.object(s.CLI,'load',return_value=registry),patch.object(s,'get_settings',return_value=settings):
            snap=m.snapshot()
        self.assertTrue(snap['instances'][0]['stale'])
        self.assertEqual(snap['instances'][0]['region'],'cqa1')
        self.assertEqual(registry,original)

if __name__=='__main__':unittest.main()
