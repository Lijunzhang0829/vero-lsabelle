"""Exact, native audit evidence; stdout is never a completion handshake."""
import hashlib
import xml.etree.ElementTree as ET


PROTOCOL = 'audited-batch-worker-v2'


def validate_native_audit(check, *, prefix, delta, names, imports, label):
    content = check.get('audit_content')
    if (not isinstance(content, str)
            or hashlib.sha256(content.encode()).hexdigest() != check.get('audit_sha256')):
        raise ValueError('native batch audit missing or bytes mismatch')
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError('native batch audit is malformed XML') from error
    request = check.get('request_id')
    worker = check.get('worker')
    if (not isinstance(request, int) or isinstance(request, bool) or request < 1
            or not isinstance(worker, str) or not worker
            or root.tag != 'batch_audit' or root.attrib != {
                'version':'2', 'worker':worker, 'request':str(request), 'theory':label}):
        raise ValueError('native batch audit request/worker/theory mismatch')
    if [x.tag for x in root] != ['prefix', 'delta', 'imports', 'facts']:
        raise ValueError('native batch audit fields mismatch')
    for name, expected in [('prefix', prefix), ('delta', delta)]:
        part = root.find(name)
        if part.attrib or len(part) or (part.text or '') != expected:
            raise ValueError('native batch audit ' + name + ' differs from candidate')
    for name, tag, expected in [('imports', 'import', imports), ('facts', 'fact', names)]:
        part = root.find(name)
        if (part.attrib or any(x.tag != tag or x.attrib or len(x) for x in part)
                or [x.text or '' for x in part] != list(expected)):
            raise ValueError('native batch audit ' + name + ' mismatch')
