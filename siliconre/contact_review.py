"""Reproducible native-source contact views; registration never proves a via."""
import hashlib
import math
import numpy as np
try:
    from . import analysis_store as store
    from .image_regions import layer_path, render_region, png_bytes
    from .image_preview import image_size
except ImportError:
    import analysis_store as store
    from image_regions import layer_path, render_region, png_bytes
    from image_preview import image_size


def residual_summary(record):
    result={}
    for name,check in (('training',False),('checks',True)):
        errors=[p['error_px'] for p in record.get('residuals',[]) if p['check']==check]
        result[name]=dict(count=len(errors),max_error_px=max(errors) if errors else None,
                          median_error_px=float(np.median(errors)) if errors else None)
    return result


def source_crop(point, size, dimensions):
    if not isinstance(size,int) or isinstance(size,bool) or not 32<=size<=1024:
        raise ValueError('Contact crop size must be an integer from 32 to 1024')
    x,y=store._point(point);w,h=dimensions
    if x>=w or y>=h:raise ValueError('Contact point outside source image')
    cw,ch=min(size,w),min(size,h)
    return [min(max(0,math.floor(x-cw/2)),w-cw),min(max(0,math.floor(y-ch/2)),h-ch),cw,ch]


def prepare_review(db, project, contact_id, alignment_id='', context_alignment_id='', crop_size=256):
    contact=store.get_record(db,contact_id)
    if contact['type']!='evidence' or contact.get('kind')!='contact':
        raise ValueError('Select a contact evidence record')
    geometries=contact['geometry'];views=[];registrations=[];warnings=[];offset=None
    by_layer={g['layer']:g['points'][0] for g in geometries}
    for g in geometries:
        path=layer_path(db,project,g['layer']);point=g['points'][0]
        views.append(dict(layer=g['layer'],point=point,role='observed contact point',
                          roi=source_crop(point,crop_size,image_size(path))))
    if alignment_id:
        r=store.get_record(db,alignment_id)
        if r['type']!='alignment' or set((r['base_layer'],r['overlay_layer']))!=set(by_layer):
            raise ValueError('Contact alignment must match the two contact layers')
        base=by_layer[r['base_layer']];overlay=by_layer[r['overlay_layer']]
        m,r=store.region_matrix(db,r['id'],r['base_layer'],r['overlay_layer'],[*base,1e-6,1e-6])
        predicted=(m@[*overlay,1])[:2]
        store.region_matrix(db,r['id'],r['base_layer'],r['overlay_layer'],[*predicted,1e-6,1e-6])
        offset=dict(base_layer=r['base_layer'],observed=base,predicted=predicted.tolist(),
                    distance_px=float(np.linalg.norm(predicted-base)),
                    meaning='Geometric displacement only; not contact probability or proof')
        registrations.append(r)
    else:warnings.append('No regional registration selected; the two source points are independent observations.')
    if context_alignment_id:
        r=store.get_record(db,context_alignment_id)
        if r['type']!='alignment' or r['base_layer'] not in by_layer or r['overlay_layer'] in by_layer:
            raise ValueError('Context registration must map a third layer into a contact base layer')
        base=by_layer[r['base_layer']]
        m,r=store.region_matrix(db,r['id'],r['base_layer'],r['overlay_layer'],[*base,1e-6,1e-6])
        point=(np.linalg.inv(m)@[*base,1])[:2].tolist()
        path=layer_path(db,project,r['overlay_layer'])
        views.append(dict(layer=r['overlay_layer'],point=point,role='predicted context only; not an observed contact',
                          roi=source_crop(point,crop_size,image_size(path))))
        registrations.append(r)
    frames=[]
    for view in views:
        path=layer_path(db,project,view['layer']);gray,mask,meta=render_region(path,view['roi'],1024)
        raw=png_bytes(gray,mask);view['image_sha256']=hashlib.sha256(raw).hexdigest()
        view['source_path']=str(path);view['source_size']=list(image_size(path))
        view['output_size']=[gray.shape[1],gray.shape[0]]
        view['original_pixels_per_output_pixel']=[view['roi'][2]/gray.shape[1],view['roi'][3]/gray.shape[0]]
        frames.append(raw)
    return dict(contact_id=contact_id,contact_revision=contact['revision'],contact_status=contact['status'],
        crop_size=crop_size,views=views,registrations=[dict(record=r,residual_summary=residual_summary(r)) for r in registrations],
        geometric_offset=offset,warnings=warnings,electrically_verified=False),frames


def save_review(db, project, contact_id, expected_revision, status, note, alignment_id='',
                context_alignment_id='', crop_size=256, expected_image_hashes=None,
                expected_registration_revisions=None, actor='MCP'):
    plan,_=prepare_review(db,project,contact_id,alignment_id,context_alignment_id,crop_size)
    if expected_image_hashes!={v['layer']:v['image_sha256'] for v in plan['views']}:
        raise ValueError('Displayed source crops changed; reload before saving the review')
    revisions={p['record']['id']:p['record']['revision'] for p in plan['registrations']}
    if (expected_registration_revisions or {})!=revisions:
        raise ValueError('Displayed registrations changed; reload before saving the review')
    with store._write(db):
        current=store.get_record(db,contact_id)
        if current['revision']!=expected_revision or plan['contact_revision']!=expected_revision:
            raise ValueError('Contact changed; reload before reviewing')
        for item in plan['registrations']:
            r=item['record']
            if store.get_record(db,r['id'])['revision']!=r['revision']:
                raise ValueError('Registration changed; reload the contact views')
        provenance=dict(current.get('provenance',{}));snapshot=dict(plan)
        snapshot['registrations']=[dict(id=r['record']['id'],revision=r['record']['revision'],
             residual_summary=r['residual_summary']) for r in plan['registrations']]
        provenance['contact_review']=snapshot
        refs=list(dict.fromkeys([*current.get('references',[]),*[r['record']['id'] for r in plan['registrations']]]))
        return store.save_evidence(db,'contact',current['geometry'],current['label'],status,note,refs,
                                   contact_id,expected_revision,actor,provenance)
