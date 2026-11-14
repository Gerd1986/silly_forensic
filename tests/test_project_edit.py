import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'siliconre'))
from project_edit import edit_project,fit_alignment
from layer_alignment import load_alignment,similarity_matrix


class ProjectEditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root/'project.sqlite3'
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE layers(name TEXT PRIMARY KEY,path TEXT)')
            db.executemany('INSERT INTO layers VALUES(?,?)',[('M1','a.tif'),('poly','b.tif')])
            db.commit()

    def read(self,sql):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute(sql).fetchall()

    def test_fit_rotation_scale_translation_and_residuals(self):
        source=np.array([[0,0],[120,0],[0,70],[40,80]])
        expected=similarity_matrix(15,-27,32,1.2)
        target=(expected[:2,:2]@source.T).T+expected[:2,2]
        result=fit_alignment(target.tolist(),source.tolist())
        np.testing.assert_allclose(result['source_to_base'],expected,atol=1e-10)
        self.assertLess(result['rms_error_px'],1e-10)
        target[3,0]+=5
        self.assertGreater(fit_alignment(target.tolist(),source.tolist())['rms_error_px'],0.5)
        for a,b in [([[0,0],[1,1]],[[2,2],[2,2]]),([[0,0]],[[0,0]]),
                    ([[0,0],[1,1]],[[0,0],[float('nan'),1]])]:
            with self.assertRaises(ValueError):
                fit_alignment(a,b)

    def test_atomic_batch_backup_and_audit(self):
        result=edit_project(self.root,[dict(action='marker',layer='M1',x=10,y=20,label='TPI'),
            dict(action='evidence',kind='polyline',layer='M1',data={'points':[[1,2],[3,4],[8,9]]}),
            dict(action='alignment',base_layer='M1',overlay_layer='poly',dx=4,dy=8)])
        self.assertEqual(len(result['results']),3)
        with closing(sqlite3.connect(result['backup'])) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM layers').fetchone()[0],2)
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='markers'").fetchone())
        before=self.read('SELECT * FROM markers')
        with self.assertRaises(ValueError):
            edit_project(self.root,[dict(action='marker',layer='M1',x=1,y=2),
                                    dict(action='marker',layer='missing',x=1,y=2)])
        self.assertEqual(self.read('SELECT * FROM markers'),before)
        self.assertEqual(len(self.read('SELECT * FROM mcp_edit_log')),1)
        with closing(sqlite3.connect(self.path)) as db:
            np.testing.assert_allclose(load_alignment(db,'poly','M1'),np.linalg.inv(similarity_matrix(4,8)))

    def test_graph_links_and_rejection_cleanup(self):
        r=edit_project(self.root,[dict(action='trace_candidate',layer='M1',x=i,y=0,
              width=5,height=5,status='accepted') for i in range(2)]+[
              dict(action='via',layer='M1',x=1,y=2,radius=2,status='accepted'),
              dict(action='via',layer='poly',x=1,y=2,radius=2,status='accepted')])
        a,b,v,w=[o['id'] for o in r['results']]
        edit_project(self.root,[dict(action='trace_link',layer='M1',source_id=a,target_id=b),
            dict(action='via_trace_link',via_id=v,trace_id=a),
            dict(action='via_layer_link',via_a=v,via_b=w)])
        edit_project(self.root,[dict(action='update',kind='trace_candidate',id=a,fields={'status':'rejected'})])
        self.assertFalse(self.read('SELECT * FROM candidate_links'))
        self.assertFalse(self.read('SELECT * FROM via_trace_links'))
        edit_project(self.root,[dict(action='delete',kind='via',id=w)])
        self.assertFalse(self.read('SELECT * FROM via_layer_links'))

    def test_bad_edits_and_dependency_guard(self):
        ids=edit_project(self.root,[dict(action='trace_candidate',layer='M1',x=0,y=0,
             width=5,height=5,status='accepted') for _ in range(3)])['results']
        a,b,c=[o['id'] for o in ids]
        edit_project(self.root,[dict(action='logic_cell',layer='M1',name='FF1',kind='DFF',pins={'D':a,'Q':b,'CLK':c})])
        with self.assertRaises(ValueError):
            edit_project(self.root,[dict(action='delete',kind='trace_candidate',id=a)])
        for op in [dict(action='update',kind='marker; DROP TABLE layers',id=1,fields={'x':3}),
                   dict(action='marker',layer='M1',x=float('inf'),y=0),
                   dict(action='alignment',base_layer='M1',overlay_layer='poly',dx=0,dy=0,scale=0),
                   dict(action='update',kind='trace_candidate',id=a,fields={'layer':'poly'}),
                   dict(action='trace_candidate',layer='M1',x=0,y=0,width=-1,height=1)]:
            with self.assertRaises((ValueError,KeyError)):
                edit_project(self.root,[op])
        self.assertEqual(len(self.read('SELECT * FROM layers')),2)


if __name__=='__main__':
    unittest.main()
