"""Exercise real stdio MCP serialization and image responses on a small project."""
import asyncio
import base64
import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
import numpy as np
from PIL import Image
import tifffile
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from siliconre.layer_alignment import save_alignment


class MCPImageTests(unittest.TestCase):
    def test_stdio_images_metadata_validation_and_read_only_project(self):
        async def exercise(folder):
            params = StdioServerParameters(
                command=sys.executable,
                args=[str(Path(__file__).resolve().parents[1]/'siliconre'/'mcp_server.py')],
                env={**os.environ, 'SILICONRE_PROJECT': str(folder)})
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    names = {t.name for t in (await session.list_tools()).tools}
                    self.assertTrue({'get_layer_info','get_layer_overview','get_layer_region',
                                     'get_layer_alignment','compare_layer_region','plan_layer_scan'} <= names)
                    for tool,args,count in [
                        ('get_layer_overview',{'layer':'M1','max_side':64},1),
                        ('get_layer_region',{'layer':'M1','x':0,'y':0,'width':48,'height':32,'max_side':64},1),
                        ('compare_layer_region',{'base_layer':'M1','overlay_layer':'poly',
                         'x':0,'y':0,'width':48,'height':32,'max_side':64},3)]:
                        result = await session.call_tool(tool,args)
                        self.assertFalse(result.isError, str(result.content))
                        images = [c for c in result.content if c.type=='image']
                        self.assertEqual(len(images),count)
                        metadata = json.loads(result.content[0].text)
                        self.assertIn('coordinate_frame',metadata)
                        for block in images:
                            self.assertEqual(block.mimeType,'image/png')
                            with Image.open(io.BytesIO(base64.b64decode(block.data))) as image:
                                pixels = np.array(image)
                            np.testing.assert_array_equal(pixels[:,:,0], data)
                            self.assertTrue((pixels[:,:,3]==255).all())
                    for tool,args in [
                        ('get_layer_info',{'layer':'M1'}),
                        ('get_layer_alignment',{'base_layer':'poly','overlay_layer':'M1'}),
                        ('plan_layer_scan',{'layer':'M1','tile_width':20,'tile_height':20,
                                           'overlap_px':4,'limit':2})]:
                        result = await session.call_tool(tool,args)
                        self.assertFalse(result.isError, str(result.content))
                    for tool,args in [
                        ('get_layer_overview',{'layer':'missing'}),
                        ('get_layer_overview',{'layer':'M1','max_side':10000}),
                        ('compare_layer_region',{'base_layer':'M1','overlay_layer':'unaligned',
                         'x':0,'y':0,'width':48,'height':32}),
                        ('get_layer_region',{'layer':'M1','x':0,'y':0,'width':-1,'height':5})]:
                        result = await session.call_tool(tool,args)
                        self.assertTrue(result.isError)

        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            data = (np.arange(32*48).reshape(32,48)%256).astype(np.uint8)
            tifffile.imwrite(folder/'M1.tif',data)
            tifffile.imwrite(folder/'poly.tif',np.rot90(data))
            with closing(sqlite3.connect(folder/'project.sqlite3')) as db:
                with db:
                    db.execute('CREATE TABLE layers (name TEXT PRIMARY KEY,path TEXT)')
                    db.executemany('INSERT INTO layers VALUES (?,?)',
                                   [('M1','M1.tif'),('poly','poly.tif'),('unaligned','M1.tif')])
                    save_alignment(db,'M1','poly',48,0,90,1)
            before = hashlib.sha256((folder/'project.sqlite3').read_bytes()).digest()
            asyncio.run(exercise(folder))
            self.assertEqual(hashlib.sha256((folder/'project.sqlite3').read_bytes()).digest(), before)
