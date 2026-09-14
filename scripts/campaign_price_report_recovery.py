"""Reclassify saved price feedback under the same user-approved two-yuan rule."""
from campaign_entry_authority import load
from campaign_feedback_normalization import normalize_errors


def reclassify(transport,report,payload,folder):
    if report.get('prior_terminal_sha256'):
        from campaign_prior_failure_import import import_prior
        from campaign_entry_authority import file_sha
        ref=transport.root/'prior-import'/(str(report['batch'])+'-readback-report.json')
        if not ref.exists():return report  # Initial custom-only batch needs no new read.
        imported=import_prior(transport,payload,payload['items'],readback=True)
        if set(imported)!=set(payload['items']):raise ValueError('prior_price_readback_scope_not_verified')
        value=load(ref)
        if (value['source_terminal']!=report['source_terminal'] or value['bundle_id']!=report['bundle_id']
                or file_sha(value['source_terminal'])!=report['prior_terminal_sha256']):
            raise ValueError('prior_price_report_binding_changed')
        return value
    from campaign_failure_remediation import report_from_download
    terminal=load(transport.root/'terminals'/('signup-'+str(report['batch'])+'.json'))
    if not terminal.get('errors'):terminal=report_from_download(transport,terminal,payload,folder)
    body=transport.authority.get_bundle(report['bundle_id']);snapshot=load(body['snapshot_path'])
    saved=load(transport.root/'verified-discounts'/(report['bundle_id']+'.json'))
    actual=[dict(r,verified_readback=True,evidence=saved.get('evidence'),target=body['target']) for r in saved['rows']]
    errors=normalize_errors(terminal,submitted_rows=body['signup_rows'],erp_rows=snapshot['all_erp_rows'],
        fixed_bases=transport.authority.bases(snapshot),actual_discounts=actual,
        rate=body['official_rate'],target_mode=body['target'])
    wanted={(e['item'],e['sku']) for e in report['errors']}
    return dict(report,errors=[e for e in errors if (e['item'],e['sku']) in wanted])
