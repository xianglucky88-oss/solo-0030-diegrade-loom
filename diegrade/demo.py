from pathlib import Path

from . import intake, rules, wafers
from .codec import decode

EXAMPLES = Path(__file__).resolve().parent.parent / 'examples'


def install(conn):
    sample = decode((EXAMPLES / 'sample.json').read_text(encoding='utf-8'))
    rules.register(conn, sample['rule'])
    spec = wafers.register(conn, sample['wafer'])
    _, revision = wafers.load_wafer(conn, spec['id'])
    if not revision:
        for source in sample['batches']:
            source = dict(source)
            source['csv'] = (EXAMPLES / source['csv']).read_text(encoding='utf-8')
            result = intake.intake(conn, spec['id'], {'expected': revision, 'batch': source}, write=True)
            revision = result['revision']
    return spec['id']
