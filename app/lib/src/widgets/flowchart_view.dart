import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../models/quasar_models.dart';

class FlowchartView extends StatelessWidget {
  const FlowchartView({
    super.key,
    required this.diagramSource,
    this.onNodeTap,
  });

  final String diagramSource;
  final ValueChanged<FlowchartNode>? onNodeTap;

  @override
  Widget build(BuildContext context) {
    final FlowchartGraph graph = parseMermaidFlowchart(diagramSource);
    if (graph.nodes.isEmpty) {
      return Container(
        width: double.infinity,
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: const Color(0xFF0D1117),
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: const Color(0xFF30363D)),
        ),
        child: SelectableText(
          diagramSource,
          style: const TextStyle(
            fontFamily: 'monospace',
            fontSize: 12,
            color: Color(0xFFC9D1D9),
          ),
        ),
      );
    }

    // Compute topological layers (Sugiyama-lite rank assignment)
    final Map<String, int> inDegree = <String, int>{
      for (final FlowchartNode n in graph.nodes) n.id: 0,
    };
    final Map<String, List<String>> outgoing = <String, List<String>>{
      for (final FlowchartNode n in graph.nodes) n.id: <String>[],
    };
    for (final FlowchartEdge e in graph.edges) {
      if (inDegree.containsKey(e.to) && outgoing.containsKey(e.from)) {
        inDegree[e.to] = (inDegree[e.to] ?? 0) + 1;
        outgoing[e.from]!.add(e.to);
      }
    }

    final Map<String, int> rank = <String, int>{};
    final List<String> queue = <String>[
      for (final FlowchartNode n in graph.nodes)
        if ((inDegree[n.id] ?? 0) == 0) n.id,
    ];
    if (queue.isEmpty) {
      queue.add(graph.nodes.first.id);
    }
    for (final String id in queue) {
      rank[id] = 0;
    }

    int head = 0;
    while (head < queue.length) {
      final String cur = queue[head++];
      final int curRank = rank[cur] ?? 0;
      for (final String nxt in outgoing[cur] ?? const <String>[]) {
        final int candidate = curRank + 1;
        if (!rank.containsKey(nxt) ||
            (candidate > rank[nxt]! && candidate < graph.nodes.length)) {
          rank[nxt] = candidate;
          queue.add(nxt);
        }
      }
    }
    for (final FlowchartNode n in graph.nodes) {
      rank.putIfAbsent(n.id, () => 0);
    }

    final Map<int, List<FlowchartNode>> layers = <int, List<FlowchartNode>>{};
    int maxRank = 0;
    for (final FlowchartNode n in graph.nodes) {
      final int r = rank[n.id] ?? 0;
      maxRank = math.max(maxRank, r);
      layers.putIfAbsent(r, () => <FlowchartNode>[]).add(n);
    }

    const double nodeWidth = 220;
    const double nodeHeight = 68;
    const double colGap = 78;
    const double rowGap = 24;
    const double pad = 28;

    int maxRowsInLayer = 1;
    for (final List<FlowchartNode> l in layers.values) {
      maxRowsInLayer = math.max(maxRowsInLayer, l.length);
    }

    final bool lr = !graph.isTopDown;
    final double canvasWidth = lr
        ? pad * 2 + (maxRank + 1) * nodeWidth + maxRank * colGap
        : pad * 2 + maxRowsInLayer * nodeWidth + (maxRowsInLayer - 1) * rowGap;
    final double canvasHeight = lr
        ? pad * 2 + maxRowsInLayer * nodeHeight + (maxRowsInLayer - 1) * rowGap
        : pad * 2 + (maxRank + 1) * nodeHeight + maxRank * colGap;

    final Map<String, Rect> nodeRects = <String, Rect>{};
    for (int r = 0; r <= maxRank; r++) {
      final List<FlowchartNode> colNodes = layers[r] ?? const <FlowchartNode>[];
      for (int idx = 0; idx < colNodes.length; idx++) {
        final FlowchartNode n = colNodes[idx];
        if (lr) {
          final double totalColH =
              colNodes.length * nodeHeight + (colNodes.length - 1) * rowGap;
          final double startY = (canvasHeight - totalColH) / 2;
          final double x = pad + r * (nodeWidth + colGap);
          final double y = startY + idx * (nodeHeight + rowGap);
          nodeRects[n.id] = Rect.fromLTWH(x, y, nodeWidth, nodeHeight);
        } else {
          final double totalRowW =
              colNodes.length * nodeWidth + (colNodes.length - 1) * rowGap;
          final double startX = (canvasWidth - totalRowW) / 2;
          final double x = startX + idx * (nodeWidth + rowGap);
          final double y = pad + r * (nodeHeight + colGap);
          nodeRects[n.id] = Rect.fromLTWH(x, y, nodeWidth, nodeHeight);
        }
      }
    }

    return Container(
      height: math.min(360, math.max(190, canvasHeight + 16)),
      width: double.infinity,
      decoration: BoxDecoration(
        color: const Color(0xFF0A0E14),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: const Color(0xFF30363D)),
      ),
      clipBehavior: Clip.antiAlias,
      child: Stack(
        children: <Widget>[
          InteractiveViewer(
            constrained: false,
            boundaryMargin: const EdgeInsets.all(120),
            minScale: 0.45,
            maxScale: 2.2,
            child: SizedBox(
              width: canvasWidth,
              height: canvasHeight,
              child: Stack(
                children: <Widget>[
                  Positioned.fill(
                    child: CustomPaint(
                      painter: _FlowchartEdgesPainter(
                        edges: graph.edges,
                        nodeRects: nodeRects,
                        leftToRight: lr,
                      ),
                    ),
                  ),
                  for (final FlowchartNode node in graph.nodes)
                    if (nodeRects.containsKey(node.id))
                      Positioned.fromRect(
                        rect: nodeRects[node.id]!,
                        child: _FlowchartNodeCard(
                          node: node,
                          onTap: onNodeTap == null
                              ? null
                              : () => onNodeTap!(node),
                        ),
                      ),
                ],
              ),
            ),
          ),
          Positioned(
            right: 10,
            bottom: 8,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              decoration: BoxDecoration(
                color: const Color(0xCC161B22),
                borderRadius: BorderRadius.circular(6),
                border: Border.all(color: const Color(0xFF30363D)),
              ),
              child: const Text(
                'Pan / pinch-zoom • Click a node to jump to Tour Stop',
                style: TextStyle(fontSize: 10.5, color: Color(0xFF8B949E)),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _FlowchartNodeCard extends StatelessWidget {
  const _FlowchartNodeCard({required this.node, this.onTap});

  final FlowchartNode node;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final List<String> parts = node.label.split(RegExp(r'\s+[—–-]\s+'));
    final String headline = parts.first.trim();
    final String subtitle =
        parts.length > 1 ? parts.sublist(1).join(' — ').trim() : '';

    return Material(
      color: const Color(0xFF161B22),
      borderRadius: BorderRadius.circular(8),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(8),
        hoverColor: const Color(0xFF1F2937),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: const Color(0xFF388BFD).withValues(alpha: 0.55)),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisAlignment: MainAxisAlignment.center,
            children: <Widget>[
              if (node.group != null && node.group!.isNotEmpty)
                Text(
                  node.group!.toUpperCase(),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 9,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.6,
                    color: Color(0xFF58A6FF),
                  ),
                ),
              Text(
                headline,
                maxLines: subtitle.isEmpty ? 2 : 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                  color: Color(0xFFE6EDF3),
                ),
              ),
              if (subtitle.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 2),
                  child: Text(
                    subtitle,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontSize: 10.5,
                      color: Color(0xFF8B949E),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _FlowchartEdgesPainter extends CustomPainter {
  _FlowchartEdgesPainter({
    required this.edges,
    required this.nodeRects,
    required this.leftToRight,
  });

  final List<FlowchartEdge> edges;
  final Map<String, Rect> nodeRects;
  final bool leftToRight;

  @override
  void paint(Canvas canvas, Size size) {
    final Paint stroke = Paint()
      ..color = const Color(0xFF58A6FF).withValues(alpha: 0.72)
      ..strokeWidth = 1.7
      ..style = PaintingStyle.stroke;
    final Paint fill = Paint()
      ..color = const Color(0xFF58A6FF).withValues(alpha: 0.85)
      ..style = PaintingStyle.fill;

    for (final FlowchartEdge edge in edges) {
      final Rect? fromR = nodeRects[edge.from];
      final Rect? toR = nodeRects[edge.to];
      if (fromR == null || toR == null) {
        continue;
      }

      final Offset start = leftToRight
          ? Offset(fromR.right, fromR.center.dy)
          : Offset(fromR.center.dx, fromR.bottom);
      final Offset end = leftToRight
          ? Offset(toR.left, toR.center.dy)
          : Offset(toR.center.dx, toR.top);

      final Path path = Path()..moveTo(start.dx, start.dy);
      if (leftToRight) {
        final double midX = (start.dx + end.dx) / 2;
        path.cubicTo(midX, start.dy, midX, end.dy, end.dx, end.dy);
      } else {
        final double midY = (start.dy + end.dy) / 2;
        path.cubicTo(start.dx, midY, end.dx, midY, end.dx, end.dy);
      }
      canvas.drawPath(path, stroke);

      // Draw arrowhead pointing into `end`
      const double arrowSize = 6.5;
      final double angle = leftToRight
          ? (end.dx >= start.dx ? 0.0 : math.pi)
          : (end.dy >= start.dy ? math.pi / 2 : -math.pi / 2);
      final Path head = Path()
        ..moveTo(end.dx, end.dy)
        ..lineTo(
          end.dx - arrowSize * math.cos(angle - 0.48),
          end.dy - arrowSize * math.sin(angle - 0.48),
        )
        ..lineTo(
          end.dx - arrowSize * math.cos(angle + 0.48),
          end.dy - arrowSize * math.sin(angle + 0.48),
        )
        ..close();
      canvas.drawPath(head, fill);
    }
  }

  @override
  bool shouldRepaint(covariant _FlowchartEdgesPainter oldDelegate) {
    return oldDelegate.edges != edges ||
        oldDelegate.nodeRects != nodeRects ||
        oldDelegate.leftToRight != leftToRight;
  }
}
