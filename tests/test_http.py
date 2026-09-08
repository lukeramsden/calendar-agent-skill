from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import pytest
from calendar_cli.sources import Transport, CalendarError, xml
from calendar_cli.documents import parse_bound

@pytest.fixture
def feed_server(basic, monkeypatch):
    state = {'raw': basic, 'etag': '"one"', 'requests': [], 'redirect': False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            state['requests'].append(dict(self.headers))
            if state['redirect']:
                self.send_response(302)
                self.send_header('Location', 'https://another.example.test/private')
                self.end_headers()
            elif self.headers.get('If-None-Match') == state['etag']:
                self.send_response(304)
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header('ETag', state['etag'])
                self.end_headers()
                self.wfile.write(state['raw'])
    # HTTPServer's display hostname should not require external reverse DNS.
    with monkeypatch.context() as context:
        context.setattr('socket.getfqdn', lambda name='': 'localhost')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}/secret-token.ics', state
    server.shutdown()
    server.server_close()
    thread.join()


def test_http_validators_new_window(store, feed_server):
    url, state = feed_server
    source = store.add_source('feed', 'feed', {'url': url, 'allow_http': True})
    a, b, c = map(parse_bound, ['2026-03-01', '2026-03-10', '2026-04-01'])
    cid = store.sync(source['id'], a, b)['calendars'][0]
    store.sync(source['id'], a, c)
    assert state['requests'][-1]['If-None-Match'] == '"one"'
    assert not store.coverage(cid, a, c)['gaps']
    assert 'secret-token' not in str(store.status())


def test_redirect_and_xml_safety(feed_server):
    url, state = feed_server
    with pytest.raises(CalendarError, match='HTTP'):
        Transport({'url': url})
    state['redirect'] = True
    with pytest.raises(CalendarError, match='Cross-origin'):
        Transport({'url': url, 'allow_http': True, 'bearer': 'private'}).feed()
    with pytest.raises(CalendarError):
        xml(b'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><a>&x;</a>')
