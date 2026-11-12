"""Real MCP write/read/undo roundtrip; also runs against the packaged Windows EXE."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
import numpy as np
import tifffile
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPResearchTests(unittest.TestCase):
    def test_persistent_writes_regional_comparison_guards_and_undo(self):
        async def exercise(folder):
            exe=os.environ.get('SILICONRE_TEST_MCP_EXECUTABLE')
            params=StdioServerParameters(command=exe or sys.executable,
                args=[] if exe else [str(Path(__file__).resolve().parents[1]/'siliconre'/'mcp_server.py')],
                env={**os.environ,'SILICONRE_PROJECT':str(folder)})
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    async def call(name,args,error=False):
                        result=await session.call_tool(name,args)
                        self.assertEqual(bool(result.isError),error,str(result.content))
                        if error:return result
                        if result.structuredContent is not None:
                            value=result.structuredContent
                            return value.get('result',value) if isinstance(value,dict) else value
                        return json.loads(result.content[0].text)
                    names={t.name for t in (await session.list_tools()).tools}
                    self.assertTrue({'save_regional_alignment','save_analysis_evidence','undo_analysis_change',
                        'redo_analysis_change','delete_analysis_record','list_analysis_history'}<=names)
                    await call('list_analysis_records',{})
                    with closing(sqlite3.connect(folder/'project.sqlite3')) as db:
                        self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='analysis_records'").fetchone())
                    region=await call('save_regional_alignment',dict(base_layer='M1',overlay_layer='M2',
                        roi=[0,0,48,32],name='contact crop',reference_points=[
                            dict(source=[1,1],target=[1,1]),dict(source=[30,1],target=[30,1]),
                            dict(source=[5,20],target=[5,20],check=True)]))
                    args=dict(base_layer='M1',overlay_layer='M2',x=0,y=0,width=48,height=32,
                              max_side=64,alignment_id=region['id'])
                    result=await session.call_tool('compare_layer_region',args)
                    self.assertFalse(result.isError,str(result.content))
                    self.assertEqual(len([c for c in result.content if c.type=='image']),3)
                    await call('compare_layer_region',{**args,'width':49},error=True)
                    point=await call('save_analysis_evidence',dict(kind='contact',label='possible via',
                        references=[region['id']],geometry=[dict(layer='M1',points=[[5,5]]),dict(layer='M2',points=[[5,5]])]))
                    await call('save_analysis_evidence',dict(kind='point',geometry=[dict(layer='M1',points=[[1000,0]])]),error=True)
                    await call('delete_analysis_record',dict(record_id=region['id'],expected_revision=region['revision']),error=True)
                    edit=dict(kind='contact',label='reviewed via',status='confirmed',note='Inspected both native crops',
                        references=[region['id']],geometry=point['geometry'],record_id=point['id'],expected_revision=point['revision'])
                    updated=await call('save_analysis_evidence',edit)
                    await call('save_analysis_evidence',edit,error=True)
                    hist=await call('list_analysis_history',{})
                    seq=hist[0]['seq']
                    await call('undo_analysis_change',dict(expected_seq=seq))
                    await call('redo_analysis_change',dict(expected_seq=seq))
                    records=await call('list_analysis_records',dict(record_type='evidence'))
                    self.assertEqual(records[0]['status'],'confirmed')
                    self.assertGreater(records[0]['revision'],updated['revision'])
                    await call('delete_analysis_record',dict(record_id=records[0]['id'],expected_revision=records[0]['revision']))
                    hist=await call('list_analysis_history',{})
                    await call('undo_analysis_change',dict(expected_seq=hist[0]['seq']))
                    preview=await call('preview_trace_paths',dict(layer='M1',x=0,y=0,width=48,height=32,threshold=125))
                    self.assertTrue(preview['paths'])
                    self.assertGreater(len(preview['paths'][0]['points']),2)
                    extracted=await call('extract_trace_paths',dict(layer='M1',x=0,y=0,width=48,height=32,threshold=125))
                    self.assertEqual(len(extracted['records']),1)
                    fit=await call('preview_reference_fit',dict(reference_points=region['reference_points']))
                    self.assertEqual(fit['residual_summary']['checks']['count'],1)
                    viewed=await session.call_tool('get_contact_review',dict(contact_id=point['id'],alignment_id=region['id'],crop_size=32))
                    self.assertFalse(viewed.isError,str(viewed.content))
                    self.assertEqual(len([v for v in viewed.content if v.type=='image']),2)
                    plan=json.loads(viewed.content[0].text)
                    hashes={v['layer']:v['image_sha256'] for v in plan['views']}
                    revisions={r['record']['id']:r['record']['revision'] for r in plan['registrations']}
                    review=dict(contact_id=point['id'],expected_revision=plan['contact_revision'],status='confirmed',
                        note='Viewed native source contact crops',expected_image_hashes=hashes,
                        expected_registration_revisions=revisions,alignment_id=region['id'],crop_size=32)
                    reviewed=await call('review_contact',review)
                    self.assertEqual(reviewed['provenance']['contact_review']['views'][0]['image_sha256'],hashes['M1'])
                    await call('review_contact',review,error=True)
                    history=await call('list_analysis_history',{})
                    await call('undo_analysis_change',dict(expected_seq=history[0]['seq']))
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            image=np.zeros((32,48),np.uint8);image[14:18,4:40]=255
            for name in ('M1','M2'):tifffile.imwrite(root/(name+'.tif'),image)
            before={n:hashlib.sha256((root/(n+'.tif')).read_bytes()).hexdigest() for n in ('M1','M2')}
            with closing(sqlite3.connect(root/'project.sqlite3')) as db:
                db.execute('CREATE TABLE layers(name TEXT PRIMARY KEY,path TEXT)')
                db.executemany('INSERT INTO layers VALUES (?,?)',[(n,n+'.tif') for n in ('M1','M2')]);db.commit()
            asyncio.run(exercise(root))
            for n in before:self.assertEqual(hashlib.sha256((root/(n+'.tif')).read_bytes()).hexdigest(),before[n])
            with closing(sqlite3.connect(root/'project.sqlite3')) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM analysis_records').fetchone()[0],3)
                self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='via_layer_links'").fetchone())


if __name__=='__main__':unittest.main()
