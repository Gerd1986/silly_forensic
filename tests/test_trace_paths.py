from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import cv2
import numpy as np
import tifffile
from siliconre.trace_paths import extract_paths,save_paths,skeleton_chains
from siliconre import analysis_store as store


class TracePathTests(unittest.TestCase):
    def extract(self,image,roi=None):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.tif';tifffile.imwrite(path,image,bigtiff=True)
            original=path.read_bytes()
            result=extract_paths(path,roi or [0,0,image.shape[1],image.shape[0]],threshold=125,min_area=5)
            self.assertEqual(path.read_bytes(),original)
        for route in result['paths']:
            for x,y in route['points']:
                self.assertGreater(image[int(y-.5),int(x-.5)],125)
            for a,b in zip(route['points'],route['points'][1:]):
                self.assertLessEqual(np.linalg.norm(np.array(a)-b),2**.5+1e-9)
        return result

    def test_bent_line_is_ordered_source_path_not_bbox(self):
        a=np.zeros((140,160),np.uint8)
        cv2.polylines(a,[np.array([[20,30],[90,30],[90,100],[140,100]],np.int32)],False,255,7)
        r=self.extract(a,[10,10,145,120])
        self.assertEqual(len(r['paths']),1)
        p=r['paths'][0]
        self.assertGreater(p['length_px'],160)
        self.assertFalse(p['closed'])
        self.assertFalse(p['component_touches_roi_border'])
        self.assertTrue(any(abs(x-90)<5 and y>60 for x,y in p['points']))
        self.assertFalse(r['gap_bridging'])

    def test_parallel_lines_and_gap_are_not_joined(self):
        a=np.zeros((100,180),np.uint8)
        a[20:25,10:80]=255;a[20:25,90:160]=255;a[50:55,10:160]=255
        r=self.extract(a)
        self.assertEqual(len(r['paths']),3)
        self.assertEqual(len({p['component'] for p in r['paths']}),3)

    def test_t_branch_splits_at_junction(self):
        a=np.zeros((120,120),np.uint8)
        cv2.line(a,(20,30),(100,30),255,5);cv2.line(a,(60,30),(60,100),255,5)
        r=self.extract(a)
        self.assertTrue(r['junctions'])
        self.assertGreaterEqual(len(r['paths']),3)
        self.assertTrue(any(p['start_kind']=='junction' or p['end_kind']=='junction' for p in r['paths']))

    def test_closed_loop_retains_closure(self):
        a=np.zeros((100,100),np.uint8);cv2.circle(a,(50,50),30,255,5)
        r=self.extract(a)
        self.assertEqual(len(r['paths']),1)
        self.assertTrue(r['paths'][0]['closed'])
        self.assertEqual(r['paths'][0]['points'][0],r['paths'][0]['points'][-1])

    def test_roi_clip_flag_and_guard(self):
        a=np.zeros((100,100),np.uint8);a[40:47,:]=255
        r=self.extract(a,[20,20,40,60])
        self.assertTrue(r['paths'][0]['component_touches_roi_border'])
        with self.assertRaises(ValueError):extract_paths('not-read',[0,0,2000,2000])
        with self.assertRaises(ValueError):extract_paths('not-read',[0,0,20,20],threshold=300)

    def test_single_pixel_graph_does_not_invent_edges(self):
        a=np.zeros((5,5),bool);a[2,2]=True
        paths,graph=skeleton_chains(a)
        self.assertEqual(paths,[]);self.assertEqual(graph[(2,2)],[])

    def test_failed_batch_is_atomic_and_success_stores_polylines(self):
        db=sqlite3.connect(':memory:')
        try:
            db.execute('CREATE TABLE layers(name TEXT,path TEXT)');db.execute("INSERT INTO layers VALUES ('M1','image')");db.commit()
            a=np.zeros((100,100),np.uint8);a[10:15,10:90]=255;a[50:55,10:90]=255
            r=self.extract(a)
            real=store.save_evidence;calls=[]
            def fail_second(*args,**kw):
                calls.append(1)
                if len(calls)==2:raise ValueError('injected')
                return real(*args,**kw)
            with patch('siliconre.trace_paths.store.save_evidence',side_effect=fail_second):
                with self.assertRaises(ValueError):save_paths(db,'M1',r)
            self.assertEqual(store.list_records(db),[])
            saved=save_paths(db,'M1',r)
            self.assertEqual(len(saved),2)
            evidence=store.list_records(db)
            self.assertEqual(evidence[0]['status'],'candidate')
            self.assertGreater(len(evidence[0]['geometry'][0]['points']),2)
            self.assertEqual(evidence[0]['provenance']['threshold'],125)
            self.assertFalse(evidence[0]['provenance']['electrically_verified'])
        finally:db.close()


if __name__=='__main__':unittest.main()
