import 'dart:convert';
import 'package:http/http.dart' as http;
import '../models/quasar_models.dart';

class QuasarApiClient {
  QuasarApiClient({required this.baseUrl, http.Client? client})
      : _client = client ?? http.Client();

  String baseUrl;
  final http.Client _client;

  Uri _uri(String path, [Map<String, String>? query]) {
    final Uri parsed = Uri.parse('$baseUrl$path');
    if (query == null || query.isEmpty) {
      return parsed;
    }
    return parsed.replace(queryParameters: query);
  }

  Future<QuasarStatePayload> fetchState({String? sessionId}) async {
    final Map<String, String> query = <String, String>{
      if (sessionId != null && sessionId.isNotEmpty) 'sessionId': sessionId,
    };
    final http.Response resp = await _client
        .get(_uri('/api/state', query))
        .timeout(const Duration(seconds: 8));
    if (resp.statusCode != 200) {
      throw Exception('Backend returned HTTP ${resp.statusCode}');
    }
    final Map<String, dynamic> decoded =
        jsonDecode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
    return QuasarStatePayload.fromJson(decoded);
  }

  Future<Map<String, dynamic>> _postJson(
    String path,
    Map<String, dynamic> payload, {
    Duration timeout = const Duration(seconds: 20),
  }) async {
    final http.Response resp = await _client
        .post(
          _uri(path),
          headers: const <String, String>{
            'Content-Type': 'application/json; charset=utf-8',
          },
          body: jsonEncode(payload),
        )
        .timeout(timeout);
    Map<String, dynamic> decoded = <String, dynamic>{};
    final String bodyText = utf8.decode(resp.bodyBytes);
    if (bodyText.trim().isNotEmpty) {
      try {
        decoded = jsonDecode(bodyText) as Map<String, dynamic>;
      } catch (_) {
        decoded = <String, dynamic>{'error': bodyText};
      }
    }
    if (resp.statusCode >= 400 || decoded['ok'] == false) {
      throw Exception(
        '${decoded['error'] ?? 'Request to $path failed (${resp.statusCode})'}',
      );
    }
    return decoded;
  }

  Future<String> startTutorial() async {
    final Map<String, dynamic> res =
        await _postJson('/api/tutorial', const <String, dynamic>{});
    return '${res['sessionId'] ?? ''}';
  }

  Future<void> startReview({
    String prUrl = '',
    String repoDir = '',
    String branch = '',
    String effort = 'high',
    bool commentsOnly = false,
  }) async {
    await _postJson('/api/start-review', <String, dynamic>{
      'prUrl': prUrl,
      'repoDir': repoDir,
      'branch': branch,
      'effort': effort,
      'commentsOnly': commentsOnly,
    });
  }

  Future<void> toggleStopRead({
    required String sessionId,
    required String stopId,
  }) async {
    await _postJson('/api/toggle-stop-read', <String, dynamic>{
      'sessionId': sessionId,
      'stopId': stopId,
    });
  }

  Future<String> threadAction({
    required String sessionId,
    required String threadId,
    required String action,
    Map<String, dynamic> extra = const <String, dynamic>{},
  }) async {
    final Map<String, dynamic> res =
        await _postJson('/api/thread-action', <String, dynamic>{
      'sessionId': sessionId,
      'threadId': threadId,
      'action': action,
      ...extra,
    });
    return '${res['message'] ?? 'Updated'}';
  }

  Future<void> replyInThread({
    required String sessionId,
    required String threadId,
    required String question,
    bool changeMode = false,
  }) async {
    await _postJson('/api/reply-thread', <String, dynamic>{
      'sessionId': sessionId,
      'threadId': threadId,
      'question': question,
      'changeMode': changeMode,
    });
  }

  Future<String> askAtLine({
    required String sessionId,
    required String path,
    required int line,
    required String question,
    String mode = 'qa',
    String severity = 'DESIGN',
  }) async {
    final String endpoint =
        mode == 'manual' ? '/api/add-comment' : '/api/ask-line';
    final Map<String, dynamic> res =
        await _postJson(endpoint, <String, dynamic>{
      'sessionId': sessionId,
      'path': path,
      'line': line,
      'mode': mode,
      'severity': severity,
      'question': question,
    });
    return '${res['threadId'] ?? ''}';
  }

  Future<int> fixSelected({
    required String sessionId,
    Map<String, String>? solutionChoices,
  }) async {
    final Map<String, dynamic> res =
        await _postJson('/api/fix-selected', <String, dynamic>{
      'sessionId': sessionId,
      if (solutionChoices != null && solutionChoices.isNotEmpty)
        'solutionChoices': solutionChoices,
    });
    return (res['count'] as num?)?.toInt() ?? 0;
  }

  Future<Map<String, dynamic>> publishGithubReview({
    required String sessionId,
    required String event,
    String summaryBody = '',
  }) async {
    final Map<String, dynamic> res = await _postJson(
      '/api/publish-github',
      <String, dynamic>{
        'sessionId': sessionId,
        'event': event,
        'body': summaryBody,
      },
      timeout: const Duration(seconds: 35),
    );
    if (res['clientSidePublish'] == true) {
      final String ghUrl = '${res['url'] ?? ''}';
      final String token = '${res['token'] ?? ''}';
      final Map<String, dynamic> ghPayload = Map<String, dynamic>.from(
        (res['payload'] as Map<String, dynamic>?) ?? const <String, dynamic>{},
      );
      if (event != 'PENDING') {
        ghPayload['event'] = event;
      }
      final http.Response ghResp = await _client.post(
        Uri.parse(ghUrl),
        headers: <String, String>{
          'Authorization': 'Bearer $token',
          'Accept': 'application/vnd.github+json',
          'Content-Type': 'application/json',
        },
        body: jsonEncode(ghPayload),
      );
      if (ghResp.statusCode >= 300) {
        throw Exception(
          'GitHub API returned ${ghResp.statusCode}: ${ghResp.body}',
        );
      }
    }
    return res;
  }

  Future<String> saveGithubToken(String token) async {
    String login = '';
    String name = '';
    try {
      final http.Response userResp = await _client
          .get(
            Uri.parse('https://api.github.com/user'),
            headers: <String, String>{
              'Authorization': 'Bearer ${token.trim()}',
              'Accept': 'application/vnd.github+json',
            },
          )
          .timeout(const Duration(seconds: 8));
      if (userResp.statusCode == 200) {
        final Map<String, dynamic> u =
            jsonDecode(userResp.body) as Map<String, dynamic>;
        login = '${u['login'] ?? ''}';
        name = '${u['name'] ?? ''}';
      }
    } catch (_) {}

    final Map<String, dynamic> res =
        await _postJson('/api/github-token', <String, dynamic>{
      'token': token.trim(),
      if (login.isNotEmpty) 'login': login,
      if (name.isNotEmpty) 'name': name,
    });
    return '${res['login'] ?? 'GitHub User'}';
  }

  Future<void> openInEditor({
    required String sessionId,
    required String path,
    required int line,
  }) async {
    await _postJson('/api/open-in-editor', <String, dynamic>{
      'sessionId': sessionId,
      'path': path,
      'line': line,
    });
  }

  Future<void> overrideQuarantine({
    required String sessionId,
  }) async {
    await _postJson('/api/override-quarantine', <String, dynamic>{
      'sessionId': sessionId,
    });
  }
}
