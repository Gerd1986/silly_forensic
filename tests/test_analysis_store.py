import sqlite3
import unittest
from contextlib import closing
import numpy as np
from siliconre import analysis_store as store


class ResearchStoreTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE layers(name TEXT PRIMARY KEY,path TEXT)')
        self.db.executemany('INSERT INTO layers VALUES (?,?)',[('M1','a'),('M2','b')])
        self.db.commit()

    def tearDown(self):self.db.close()

    def point(self, **kw):
        return store.save_evidence(self.db,'point',[dict(layer='M1',points=[[10,20]])],**kw)

    def test_read_old_project_is_nonmutating(self):
        before=self.db.total_changes
        self.assertEqual(store.list_records(self.db),[])
        self.assertEqual(store.history(self.db),[])
        self.assertEqual(self.db.total_changes,before)
        self.assertIsNone(self.db.execute("SELECT name FROM sqlite_master WHERE name='analysis_records'").fetchone())

    def test_source_coordinates_and_review(self):
        with self.assertRaises(ValueError):self.point(status='confirmed')
        p=self.point(status='confirmed',note='Inspected original source crop')
        self.assertEqual(p['geometry'][0]['points'],[[10,20]])
        self.assertEqual(store.list_records(self.db,status='confirmed')[0]['id'],p['id'])
        self.assertIsNone(self.db.execute("SELECT name FROM sqlite_master WHERE name='via_layer_links'").fetchone())
        for bad in (-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):
                store.save_evidence(self.db,'trace',[dict(layer='M1',points=[[0,0],[bad,3]])])
        with self.assertRaises(ValueError):
            store.save_evidence(self.db,'contact',[dict(layer='M1',points=[[1,2]])])

    def test_revisions_undo_redo_branching_and_stale_updates(self):
        p=self.point();old=p['revision']
        p=self.point(record_id=p['id'],expected_revision=p['revision'],label='new')
        with self.assertRaises(ValueError):self.point(record_id=p['id'],expected_revision=old)
        h=store.history(self.db)[0]
        with self.assertRaises(ValueError):store.undo_redo(self.db,expected_seq=h['seq']+1)
        store.undo_redo(self.db,expected_seq=h['seq'])
        restored=store.get_record(self.db,p['id'])
        self.assertEqual(restored['label'],'')
        self.assertGreater(restored['revision'],p['revision'])
        store.undo_redo(self.db,redo=True,expected_seq=h['seq'])
        self.assertEqual(store.get_record(self.db,p['id'])['label'],'new')
        store.undo_redo(self.db)
        self.point(label='new branch')
        with self.assertRaises(ValueError):store.undo_redo(self.db,redo=True)

    def test_delete_restore_and_dependencies(self):
        p=self.point()
        q=self.point(references=[p['id']])
        with self.assertRaises(ValueError):store.delete_record(self.db,p['id'],p['revision'])
        store.delete_record(self.db,q['id'],q['revision'])
        store.undo_redo(self.db)
        self.assertEqual(store.get_record(self.db,q['id'])['references'],[p['id']])

    def test_atomic_failure_and_outer_transaction(self):
        p=self.point()
        self.db.commit()
        before=len(store.history(self.db))
        with self.assertRaises(ValueError):self.point(record_id=p['id'],expected_revision=100)
        self.assertEqual(len(store.history(self.db)),before)
        self.db.execute('BEGIN')
        q=self.point(label='rolled back')
        self.db.rollback()
        with self.assertRaises(ValueError):store.get_record(self.db,q['id'])

    def test_similarity_fit_keeps_check_points_out(self):
        refs=[dict(source=[0,0],target=[10,20]),dict(source=[100,0],target=[210,20]),
              dict(source=[50,50],target=[111,121],check=True)]
        r=store.save_region(self.db,'M1','M2',[0,0,500,500],'test',refs)
        np.testing.assert_allclose(r['matrix'],[[2,0,10],[0,2,20],[0,0,1]],atol=1e-10)
        self.assertAlmostEqual(r['residuals'][-1]['error_px'],2**.5)
        self.assertEqual(r['status'],'candidate')
        with self.assertRaises(ValueError):store.save_region(self.db,'M1','M2',[0,0,500,500],'test',refs[:2],status='confirmed',note='check')

    def test_affine_and_roi_scope_no_global_change(self):
        refs=[dict(source=p,target=[1.1*p[0]+5,.9*p[1]+8]) for p in ([0,0],[100,0],[0,100])]
        r=store.save_region(self.db,'M1','M2',[5,8,200,200],'local',refs,fit_mode='affine')
        m,_=store.region_matrix(self.db,r['id'],'M1','M2',[5,8,10,10])
        np.testing.assert_allclose(m,[[1.1,0,5],[0,.9,8],[0,0,1]],atol=1e-10)
        with self.assertRaises(ValueError):store.region_matrix(self.db,r['id'],'M1','M2',[0,0,10,10])
        with self.assertRaises(ValueError):store.region_matrix(self.db,r['id'],'M2','M1',[5,8,10,10])
        self.assertIsNone(self.db.execute("SELECT name FROM sqlite_master WHERE name='alignments'").fetchone())
        with self.assertRaises(ValueError):store.fit_references(refs[:2],'affine')
        with self.assertRaises(ValueError):store.fit_references([dict(source=[1,1],target=[1,1])]*3,'affine')

    def test_create_undo_redo_monotonic_revision(self):
        p=self.point();store.undo_redo(self.db);store.undo_redo(self.db,redo=True)
        q=store.get_record(self.db,p['id'])
        self.assertGreater(q['revision'],p['revision'])
        with self.assertRaises(ValueError):self.point(record_id=p['id'],expected_revision=p['revision'])

    def test_clear_all_keeps_registrations_and_invalidates_old_undo(self):
        from siliconre.annotations import clear_annotations
        self.point()
        r=store.save_region(self.db,'M1','M2',[0,0,100,100],'keep',matrix=np.eye(3).tolist())
        clear_annotations(self.db)
        self.assertEqual([q['id'] for q in store.list_records(self.db)],[r['id']])
        with self.assertRaises(ValueError):store.undo_redo(self.db)
        with self.assertRaises(ValueError):store.undo_redo(self.db,redo=True)

    def test_evidence_keeps_alignment_revision_snapshot(self):
        r=store.save_region(self.db,'M1','M2',[0,0,100,100],'local',matrix=np.eye(3).tolist())
        e=self.point(references=[r['id']])
        store.save_region(self.db,'M1','M2',[0,0,100,100],'changed',matrix=[[1,0,5],[0,1,7],[0,0,1]],
                          record_id=r['id'],expected_revision=r['revision'])
        snapshot=store.get_record(self.db,e['id'])['reference_snapshots'][0]
        self.assertEqual(snapshot['revision'],r['revision'])
        self.assertEqual(snapshot['matrix'],np.eye(3).tolist())

    def test_persistent_journal_across_connections(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'project.sqlite3'
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE layers(name TEXT,path TEXT)');db.execute("INSERT INTO layers VALUES ('M1','a')")
                p=store.save_evidence(db,'point',[dict(layer='M1',points=[[1,1]])]);db.commit()
            with closing(sqlite3.connect(path)) as db:
                store.undo_redo(db);db.commit()
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(store.list_records(db),[])
                store.undo_redo(db,redo=True)
                self.assertEqual(store.get_record(db,p['id'])['kind'],'point')


if __name__=='__main__':unittest.main()
