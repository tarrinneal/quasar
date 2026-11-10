import 'package:flutter/material.dart';
import '../models/quasar_models.dart';
import 'flowchart_view.dart';
import 'markdown_text.dart';

class StudyGuideView extends StatelessWidget {
  const StudyGuideView({
    super.key,
    required this.session,
    required this.onJumpToStop,
    required this.onToggleStopRead,
  });

  final ReviewSession session;
  final ValueChanged<TourStop> onJumpToStop;
  final ValueChanged<TourStop> onToggleStopRead;

  void _handleFlowchartNodeTap(FlowchartNode node) {
    final String needle = node.label
        .split(RegExp(r'[\s—–:-]+'))
        .firstWhere((String s) => s.length >= 3, orElse: () => node.id)
        .toLowerCase();
    for (final TourStop s in session.allStops) {
      if (s.path.toLowerCase().contains(needle) ||
          s.title.toLowerCase().contains(needle) ||
          s.cliffsNotes.toLowerCase().contains(needle)) {
        onJumpToStop(s);
        return;
      }
    }
    if (session.allStops.isNotEmpty) {
      onJumpToStop(session.allStops.first);
    }
  }

  @override
  Widget build(BuildContext context) {
    final CliffsNotesData cn = session.cliffsNotes;
    final PrMetadata pr = session.pr;

    return ListView(
      padding: const EdgeInsets.all(20),
      children: <Widget>[
        // Overview Card
        Container(
          padding: const EdgeInsets.all(18),
          decoration: BoxDecoration(
            color: const Color(0xFF161B22),
            borderRadius: BorderRadius.circular(10),
            border: Border.all(color: const Color(0xFF30363D)),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Wrap(
                spacing: 8,
                runSpacing: 6,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: <Widget>[
                  Text(
                    pr.number != null
                        ? 'PR #${pr.number} — ${pr.title}'
                        : pr.title,
                    style: const TextStyle(
                      fontSize: 17,
                      fontWeight: FontWeight.w700,
                      color: Color(0xFFE6EDF3),
                    ),
                  ),
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 8,
                      vertical: 2.5,
                    ),
                    decoration: BoxDecoration(
                      color: const Color(0xFF21262D),
                      borderRadius: BorderRadius.circular(999),
                    ),
                    child: Text(
                      '@${pr.author} • +${pr.additions} / -${pr.deletions} • ${pr.changedFilesCount} files',
                      style: const TextStyle(
                        fontFamily: 'monospace',
                        fontSize: 11,
                        color: Color(0xFF8B949E),
                      ),
                    ),
                  ),
                ],
              ),
              if (cn.tldr.isNotEmpty) ...<Widget>[
                const SizedBox(height: 12),
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: const Color(0xFF0E1D28),
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(
                      color: const Color(0xFF388BFD).withValues(alpha: 0.35),
                    ),
                  ),
                  child: InlineMarkdownText(
                    text: '💡 **TL;DR:** ${cn.tldr}',
                    fontSize: 13.5,
                  ),
                ),
              ],
              if (cn.whatAndWhy.isNotEmpty) ...<Widget>[
                const SizedBox(height: 12),
                const Text(
                  'WHAT & WHY',
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.6,
                    color: Color(0xFF8B949E),
                  ),
                ),
                const SizedBox(height: 4),
                RichMarkdownBlock(text: cn.whatAndWhy, fontSize: 13),
              ],
            ],
          ),
        ),

        // Interactive Architecture Flowchart
        if (cn.architectureDiagram.trim().isNotEmpty) ...<Widget>[
          const SizedBox(height: 16),
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: const Color(0xFF161B22),
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: const Color(0xFF30363D)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                const Text(
                  '🗺️ ARCHITECTURE FLOWCHART',
                  style: TextStyle(
                    fontSize: 11.5,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.6,
                    color: Color(0xFF79C0FF),
                  ),
                ),
                const SizedBox(height: 10),
                FlowchartView(
                  diagramSource: cn.architectureDiagram,
                  onNodeTap: _handleFlowchartNodeTap,
                ),
              ],
            ),
          ),
        ],

        // Key Symbols & Design Trade-offs
        if (cn.keySymbols.isNotEmpty || cn.designTradeoffs.isNotEmpty) ...<Widget>[
          const SizedBox(height: 16),
          Wrap(
            spacing: 16,
            runSpacing: 16,
            children: <Widget>[
              if (cn.keySymbols.isNotEmpty)
                ConstrainedBox(
                  constraints: const BoxConstraints(minWidth: 320, maxWidth: 560),
                  child: Container(
                    padding: const EdgeInsets.all(15),
                    decoration: BoxDecoration(
                      color: const Color(0xFF161B22),
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(color: const Color(0xFF30363D)),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        const Text(
                          '🔑 KEY SYMBOLS ("CHARACTER LIST")',
                          style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.w700,
                            letterSpacing: 0.6,
                            color: Color(0xFF8B949E),
                          ),
                        ),
                        const SizedBox(height: 8),
                        for (final KeySymbol sym in cn.keySymbols)
                          Padding(
                            padding: const EdgeInsets.symmetric(vertical: 3),
                            child: InlineMarkdownText(
                              text: '• `${sym.name}` — ${sym.role}',
                              fontSize: 12.5,
                            ),
                          ),
                      ],
                    ),
                  ),
                ),
              if (cn.designTradeoffs.isNotEmpty)
                ConstrainedBox(
                  constraints: const BoxConstraints(minWidth: 320, maxWidth: 560),
                  child: Container(
                    padding: const EdgeInsets.all(15),
                    decoration: BoxDecoration(
                      color: const Color(0xFF161B22),
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(color: const Color(0xFF30363D)),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        const Text(
                          '⚖️ DESIGN TRADE-OFFS',
                          style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.w700,
                            letterSpacing: 0.6,
                            color: Color(0xFF8B949E),
                          ),
                        ),
                        const SizedBox(height: 8),
                        for (final String tr in cn.designTradeoffs)
                          Padding(
                            padding: const EdgeInsets.symmetric(vertical: 3),
                            child: InlineMarkdownText(
                              text: '• $tr',
                              fontSize: 12.5,
                            ),
                          ),
                      ],
                    ),
                  ),
                ),
            ],
          ),
        ],

        // Guided Reading Tour Chapters
        if (session.chapters.isNotEmpty) ...<Widget>[
          const SizedBox(height: 20),
          const Text(
            '🧭 GUIDED READING TOUR',
            style: TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.6,
              color: Color(0xFF79C0FF),
            ),
          ),
          const SizedBox(height: 10),
          for (final ReviewChapter ch in session.chapters)
            Container(
              margin: const EdgeInsets.only(bottom: 14),
              padding: const EdgeInsets.all(15),
              decoration: BoxDecoration(
                color: const Color(0xFF161B22),
                borderRadius: BorderRadius.circular(10),
                border: Border.all(color: const Color(0xFF30363D)),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    ch.title,
                    style: const TextStyle(
                      fontSize: 14.5,
                      fontWeight: FontWeight.w700,
                      color: Color(0xFFE6EDF3),
                    ),
                  ),
                  if (ch.summary.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 4, bottom: 8),
                      child: InlineMarkdownText(
                        text: ch.summary,
                        fontSize: 12.5,
                        color: const Color(0xFF8B949E),
                      ),
                    ),
                  for (final TourStop stop in ch.stops)
                    Container(
                      margin: const EdgeInsets.only(top: 8),
                      padding: const EdgeInsets.all(11),
                      decoration: BoxDecoration(
                        color: const Color(0xFF0D1117),
                        borderRadius: BorderRadius.circular(8),
                        border: Border.all(
                          color: stop.read
                              ? const Color(0xFF238636).withValues(alpha: 0.5)
                              : const Color(0xFF21262D),
                        ),
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: <Widget>[
                          Row(
                            children: <Widget>[
                              InkWell(
                                onTap: () => onToggleStopRead(stop),
                                child: Icon(
                                  stop.read
                                      ? Icons.check_circle
                                      : Icons.radio_button_unchecked,
                                  size: 17,
                                  color: stop.read
                                      ? const Color(0xFF3FB950)
                                      : const Color(0xFF8B949E),
                                ),
                              ),
                              const SizedBox(width: 8),
                              Expanded(
                                child: InkWell(
                                  onTap: () => onJumpToStop(stop),
                                  child: Text(
                                    '[Stop ${stop.stopNumber}] ${stop.title.isNotEmpty ? stop.title : '${stop.path}:L${stop.startLine}'}',
                                    style: const TextStyle(
                                      fontFamily: 'monospace',
                                      fontSize: 12.5,
                                      fontWeight: FontWeight.w600,
                                      color: Color(0xFF58A6FF),
                                    ),
                                  ),
                                ),
                              ),
                              OutlinedButton(
                                onPressed: () => onJumpToStop(stop),
                                style: OutlinedButton.styleFrom(
                                  visualDensity: VisualDensity.compact,
                                ),
                                child: const Text(
                                  'Open Diff →',
                                  style: TextStyle(fontSize: 11.5),
                                ),
                              ),
                            ],
                          ),
                          if (stop.cliffsNotes.isNotEmpty)
                            Padding(
                              padding: const EdgeInsets.only(top: 6, left: 25),
                              child: InlineMarkdownText(
                                text: stop.cliffsNotes,
                                fontSize: 12.5,
                              ),
                            ),
                          if (stop.whyItChanged.isNotEmpty ||
                              stop.callersAndInvariants.isNotEmpty)
                            Padding(
                              padding: const EdgeInsets.only(left: 17, top: 2),
                              child: Material(
                                color: Colors.transparent,
                                child: ExpansionTile(
                                  tilePadding:
                                      const EdgeInsets.symmetric(horizontal: 8),
                                  dense: true,
                                  title: const Text(
                                    'Why it changed & invariants',
                                    style: TextStyle(
                                      fontSize: 11.5,
                                      color: Color(0xFF8B949E),
                                    ),
                                  ),
                                  childrenPadding: const EdgeInsets.fromLTRB(
                                    8,
                                    0,
                                    8,
                                    8,
                                  ),
                                  children: <Widget>[
                                    if (stop.whyItChanged.isNotEmpty)
                                      InlineMarkdownText(
                                        text:
                                            '**Why it changed:** ${stop.whyItChanged}',
                                        fontSize: 12,
                                        color: const Color(0xFFB1BAC4),
                                      ),
                                    if (stop.callersAndInvariants.isNotEmpty)
                                      Padding(
                                        padding: const EdgeInsets.only(top: 4),
                                        child: InlineMarkdownText(
                                          text:
                                              '**Callers & invariants:** ${stop.callersAndInvariants}',
                                          fontSize: 12,
                                          color: const Color(0xFFB1BAC4),
                                        ),
                                      ),
                                  ],
                                ),
                              ),
                            ),
                        ],
                      ),
                    ),
                ],
              ),
            ),
        ],

        // Coverage Matrix
        if (session.coverageMatrix.isNotEmpty) ...<Widget>[
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.all(15),
            decoration: BoxDecoration(
              color: const Color(0xFF161B22),
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: const Color(0xFF30363D)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                const Text(
                  '🧪 TEST COVERAGE MATRIX',
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.6,
                    color: Color(0xFF8B949E),
                  ),
                ),
                const SizedBox(height: 8),
                for (final CoverageRow row in session.coverageMatrix)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 4),
                    child: InlineMarkdownText(
                      text:
                          '• **${row.status}** — ${row.behavior}${row.notes.isNotEmpty ? ' *(${row.notes})*' : ''}',
                      fontSize: 12.5,
                    ),
                  ),
              ],
            ),
          ),
        ],
      ],
    );
  }
}
