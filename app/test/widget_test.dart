import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:quasar_app/main.dart';
import 'package:quasar_app/src/models/quasar_models.dart';
import 'package:quasar_app/src/services/quasar_api_client.dart';

void main() {
  test('parseMermaidFlowchart extracts nodes, labels, and edges', () {
    const String mermaid = '''
flowchart LR
  A["Dart Lookup - getInstance()"] --> B["Native Registrar - pigeonResolveInstance"]
  B --> C["Engine Stack - Exact or Affinity Key"]
''';
    final FlowchartGraph graph = parseMermaidFlowchart(mermaid);
    expect(graph.nodes.length, 3);
    expect(graph.edges.length, 2);
    expect(graph.nodes.first.label, 'Dart Lookup - getInstance()');
  });

  testWidgets(
    'QuasarStandaloneApp renders Study Guide and 3-part GitHub Follow-Up',
    (WidgetTester tester) async {
      tester.view.physicalSize = const Size(1400, 900);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      final MockClient mockHttp = MockClient((http.Request req) async {
        if (req.url.path == '/api/state') {
          return http.Response.bytes(
            utf8.encode(jsonEncode(<String, dynamic>{
              'ok': true,
              'sidecarPort': 49341,
              'defaultWorkspaceDir': '/tmp/repo',
              'githubAuth': <String, dynamic>{
                'configured': true,
                'login': 'tarrinneal',
              },
              'agentStatus': <String, dynamic>{'active': false},
              'sessions': <Map<String, dynamic>>[
                <String, dynamic>{
                  'id': 's1',
                  'sessionId': 's1',
                  'title': 'PR #13576: Support multi-engine registration',
                  'repo': 'packages',
                },
              ],
              'filesDiff': <Map<String, dynamic>>[
                <String, dynamic>{
                  'path': 'lib/generator.dart',
                  'additions': 2,
                  'deletions': 1,
                  'rows': <Map<String, dynamic>>[
                    <String, dynamic>{
                      'type': 'mod',
                      'baseLine': 10,
                      'headLine': 10,
                      'baseText': 'if (link != null) {',
                      'headText':
                          'if (link != null && engineId == null) {',
                    },
                  ],
                },
              ],
              'activeSession': <String, dynamic>{
                'sessionId': 's1',
                'effortLevel': 'high',
                'commentsOnly': false,
                'analysisStatus': 'ready',
                'pr': <String, dynamic>{
                  'number': 13576,
                  'title': 'Support multi-engine registration',
                  'author': 'tarrinneal',
                  'additions': 120,
                  'deletions': 30,
                  'changedFilesCount': 4,
                  'isOwnedByUser': true,
                },
                'cliffsNotes': <String, dynamic>{
                  'tldr': 'Associates HostApi and FlutterApi with engine keys.',
                  'whatAndWhy': 'Fixes secondary engine detach collisions.',
                  'architectureDiagram':
                      'flowchart LR\n  A["Dart Isolate"] --> B["Engine Registry"]',
                },
                'chapters': <Map<String, dynamic>>[
                  <String, dynamic>{
                    'id': 'ch-1',
                    'title': 'Chapter 1: Generator Changes',
                    'summary': 'Updates Dart lookup logic.',
                    'stops': <Map<String, dynamic>>[
                      <String, dynamic>{
                        'id': 'stop-1',
                        'stopNumber': '1.1',
                        'title': 'generator.dart:10',
                        'path': 'lib/generator.dart',
                        'startLine': 10,
                        'endLine': 10,
                        'cliffsNotes': 'Records isolate engine affinity.',
                      },
                    ],
                  },
                ],
                'threads': <Map<String, dynamic>>[
                  <String, dynamic>{
                    'id': 'gh-inline-1',
                    'kind': 'github_existing',
                    'status': 'existing',
                    'author': 'Piinks',
                    'triageStatus': 'partially_addressed',
                    'triageBadge': '🟡 Partially Addressed',
                    'triageLabel': '🟡 Partially Addressed in Code',
                    'whatIsDone': 'Added engineId == null check.',
                    'whatIsNotDone':
                        'Negative synthetic keys skipped on stable.',
                    'whatShouldBeDone':
                        'Allow linkEngineKey < 0 on root isolates.',
                    'path': 'lib/generator.dart',
                    'startLine': 10,
                    'endLine': 10,
                    'title': '🟡 [Partially Addressed] Edge cases in affinity',
                    'draftComment': 'What happens on pre-195881 root isolates?',
                  },
                ],
              },
            })),
            200,
            headers: const <String, String>{
              'content-type': 'application/json; charset=utf-8',
            },
          );
        }
        return http.Response(jsonEncode(<String, dynamic>{'ok': true}), 200);
      });

      final QuasarApiClient api = QuasarApiClient(
        baseUrl: 'http://127.0.0.1:49341',
        client: mockHttp,
      );

      await tester.pumpWidget(QuasarStandaloneApp(apiClient: api));
      await tester.pumpAndSettle();

      expect(
        find.textContaining(
          'Associates HostApi and FlutterApi',
          findRichText: true,
        ),
        findsOneWidget,
      );
      expect(
        find.textContaining('Dart Isolate', findRichText: true),
        findsOneWidget,
      );

      // Click the GitHub comment in the left sidebar to jump directly to the diff & inline card
      await tester.tap(
        find.textContaining('Edge cases in affinity', findRichText: true).first,
      );
      await tester.pumpAndSettle();

      expect(
        find.textContaining('Added engineId == null check.', findRichText: true),
        findsOneWidget,
      );
      expect(
        find.textContaining(
          'Negative synthetic keys skipped on stable.',
          findRichText: true,
        ),
        findsOneWidget,
      );
      expect(
        find.textContaining(
          'Allow linkEngineKey < 0 on root isolates.',
          findRichText: true,
        ),
        findsOneWidget,
      );
    },
  );
}
