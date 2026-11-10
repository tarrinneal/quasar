import 'dart:io';

String resolveDefaultApiBaseUrl() {
  final String? envUrl = Platform.environment['QUASAR_API_URL'];
  if (envUrl != null && envUrl.trim().isNotEmpty) {
    return envUrl.trim().replaceAll(RegExp(r'/+$'), '');
  }
  final String port = Platform.environment['QUASAR_PORT'] ?? '49341';
  return 'http://127.0.0.1:$port';
}

Future<bool> ensureLocalBackendRunning(String baseUrl) async {
  final Uri uri = Uri.parse('$baseUrl/api/state');
  if (await _canReach(uri)) {
    return true;
  }

  final String home = Platform.environment['HOME'] ?? '';
  final List<String> candidates = <String>[
    '${Directory.current.path}/../plugin/sidecars/quasar-web/server.py',
    '${Directory.current.path}/plugin/sidecars/quasar-web/server.py',
    if (home.isNotEmpty)
      '$home/.gemini/config/plugins/quasar/sidecars/quasar-web/server.py',
  ];

  String? serverScript;
  for (final String cand in candidates) {
    final File f = File(cand);
    if (f.existsSync()) {
      serverScript = f.absolute.path;
      break;
    }
  }
  if (serverScript == null) {
    return false;
  }

  try {
    await Process.start(
      'python3',
      <String>[serverScript, '--standalone-daemon'],
      mode: ProcessStartMode.detached,
      environment: <String, String>{
        ...Platform.environment,
        'PORT': uri.hasPort ? '${uri.port}' : '49341',
      },
    );
  } catch (_) {
    return false;
  }

  for (int i = 0; i < 20; i++) {
    await Future<void>.delayed(const Duration(milliseconds: 200));
    if (await _canReach(uri)) {
      return true;
    }
  }
  return false;
}

Future<bool> _canReach(Uri uri) async {
  final HttpClient client = HttpClient()
    ..connectionTimeout = const Duration(milliseconds: 600);
  try {
    final HttpClientRequest req = await client.getUrl(uri);
    final HttpClientResponse resp = await req.close();
    await resp.drain<void>();
    return resp.statusCode == 200;
  } catch (_) {
    return false;
  } finally {
    client.close(force: true);
  }
}
