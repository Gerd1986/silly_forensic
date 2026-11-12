from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
import numpy as np
import tifffile
from siliconre import analysis_store as store
from siliconre.contact_review import prepare_review,save_review,source_crop


class ContactReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE layers(name TEXT,path TEXT)')
        for n in ('M1','M2','M3'):
            tifffile.imwrite(self.root/(n+'.tif'),np.arange(400*400,dtype=np.uint8).reshape(400,400))
            self.db.execute('INSERT INTO layers VALUES (?,?)',(n,n+'.tif'))
        self.contact=store.save_evidence(self.db,'contact',[dict(layer='M1',points=[[100,120]]),dict(layer='M2',points=[[105,127]])],label='via?')
        self.region=store.save_region(self.db,'M1','M2',[0,0,300,300],'local',[
            dict(source=[5,7],target=[0,0]),dict(source=[205,7],target=[200,0]),
            dict(source=[105,107],target=[100,101],check=True)])

    def tearDown(self):self.db.close();self.tmp.cleanup()

    def prepare(self,**kw):return prepare_review(self.db,self.root,self.contact['id'],**kw)

    def save(self,plan,**kw):
        return save_review(self.db,self.root,self.contact['id'],self.contact['revision'],'confirmed','Native crops inspected',
            alignment_id=self.region['id'],crop_size=128,
            expected_image_hashes={v['layer']:v['image_sha256'] for v in plan['views']},
            expected_registration_revisions={r['record']['id']:r['record']['revision'] for r in plan['registrations']},**kw)

    def test_two_native_views_and_holdout_error_are_read_only(self):
        before=self.db.total_changes;plan,frames=self.prepare(alignment_id=self.region['id'],crop_size=128)
        self.assertEqual(self.db.total_changes,before);self.assertEqual(len(frames),2)
        self.assertEqual(plan['views'][0]['point'],[100,120]);self.assertEqual(plan['views'][1]['point'],[105,127])
        self.assertEqual(plan['views'][0]['original_pixels_per_output_pixel'],[1,1])
        self.assertAlmostEqual(plan['registrations'][0]['residual_summary']['checks']['max_error_px'],1)
        self.assertAlmostEqual(plan['geometric_offset']['distance_px'],0,places=8)
        self.assertFalse(plan['electrically_verified'])

    def test_save_snapshot_undo_and_no_graph_edge(self):
        plan,_=self.prepare(alignment_id=self.region['id'],crop_size=128)
        saved=self.save(plan)
        self.assertEqual(saved['status'],'confirmed')
        self.assertEqual(saved['geometry'],self.contact['geometry'])
        self.assertEqual(saved['provenance']['contact_review']['views'][0]['image_sha256'],plan['views'][0]['image_sha256'])
        self.assertIsNone(self.db.execute("SELECT name FROM sqlite_master WHERE name='via_layer_links'").fetchone())
        store.undo_redo(self.db)
        self.assertEqual(store.get_record(self.db,self.contact['id'])['status'],'candidate')

    def test_changed_image_rejected(self):
        plan,_=self.prepare(alignment_id=self.region['id'],crop_size=128)
        tifffile.imwrite(self.root/'M2.tif',np.zeros((400,400),np.uint8))
        with self.assertRaisesRegex(ValueError,'source crops changed'):self.save(plan)
        self.assertEqual(store.get_record(self.db,self.contact['id'])['revision'],self.contact['revision'])

    def test_changed_alignment_even_with_identical_images_is_rejected(self):
        plan,_=self.prepare(alignment_id=self.region['id'],crop_size=128)
        store.save_region(self.db,'M1','M2',[0,0,300,300],'new',matrix=self.region['matrix'],
                          record_id=self.region['id'],expected_revision=self.region['revision'])
        with self.assertRaisesRegex(ValueError,'registrations changed'):self.save(plan)

    def test_changed_contact_rejected(self):
        plan,_=self.prepare(alignment_id=self.region['id'],crop_size=128)
        store.save_evidence(self.db,'contact',self.contact['geometry'],label='edit',record_id=self.contact['id'],expected_revision=self.contact['revision'])
        with self.assertRaisesRegex(ValueError,'Contact changed'):self.save(plan)

    def test_third_layer_predicted_context_and_wrong_scope(self):
        context=store.save_region(self.db,'M1','M3',[0,0,300,300],'context',matrix=[[1,0,-10],[0,1,-20],[0,0,1]])
        plan,frames=self.prepare(context_alignment_id=context['id'])
        self.assertEqual(len(frames),3);view=plan['views'][2]
        self.assertEqual(view['point'],[110,140]);self.assertTrue(view['role'].startswith('predicted'))
        wrong=store.save_region(self.db,'M1','M3',[200,200,20,20],'wrong',matrix=np.eye(3).tolist())
        with self.assertRaisesRegex(ValueError,'exceeds'):self.prepare(context_alignment_id=wrong['id'])
        with self.assertRaises(ValueError):self.prepare(alignment_id=context['id'])

    def test_crop_edges_and_size_validation(self):
        self.assertEqual(source_crop([0,0],128,[400,400]),[0,0,128,128])
        self.assertEqual(source_crop([399,399],128,[400,400]),[272,272,128,128])
        with self.assertRaises(ValueError):source_crop([1,1],4096,[400,400])

    def test_geometry_edit_does_not_keep_old_review_images(self):
        plan,_=self.prepare(alignment_id=self.region['id'],crop_size=128)
        saved=self.save(plan)
        edited=store.save_evidence(self.db,'contact',[dict(layer='M1',points=[[101,120]]),dict(layer='M2',points=[[105,127]])],
            record_id=saved['id'],expected_revision=saved['revision'],provenance=saved['provenance'])
        self.assertNotIn('contact_review',edited['provenance'])


if __name__=='__main__':unittest.main()
