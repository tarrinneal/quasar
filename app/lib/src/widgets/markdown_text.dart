import 'package:flutter/material.dart';

class RichMarkdownBlock extends StatelessWidget {
  const RichMarkdownBlock({
    super.key,
    required this.text,
    this.fontSize = 12.5,
    this.color = const Color(0xFFE6EDF3),
  });

  final String text;
  final double fontSize;
  final Color color;

  @override
  Widget build(BuildContext context) {
    final List<Widget> blocks = <Widget>[];
    final List<String> lines = text.split('\n');
    int i = 0;

    while (i < lines.length) {
      final String raw = lines[i];
      final String trimmed = raw.trim();

      if (trimmed.isEmpty) {
        i++;
        continue;
      }

      if (trimmed.startsWith('```')) {
        final String lang = trimmed.substring(3).trim();
        final List<String> codeLines = <String>[];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith('```')) {
          codeLines.add(lines[i]);
          i++;
        }
        if (i < lines.length) {
          i++;
        }
        blocks.add(
          Container(
            width: double.infinity,
            margin: const EdgeInsets.symmetric(vertical: 4),
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: const Color(0xFF0A0E14),
              borderRadius: BorderRadius.circular(6),
              border: Border.all(color: const Color(0xFF30363D)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                if (lang.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 4),
                    child: Text(
                      lang,
                      style: const TextStyle(
                        fontSize: 10,
                        fontWeight: FontWeight.w600,
                        color: Color(0xFF8B949E),
                      ),
                    ),
                  ),
                SelectableText(
                  codeLines.join('\n'),
                  style: TextStyle(
                    fontFamily: 'monospace',
                    fontSize: fontSize - 1,
                    height: 1.45,
                    color: const Color(0xFFC9D1D9),
                  ),
                ),
              ],
            ),
          ),
        );
        continue;
      }

      if (trimmed.startsWith('>')) {
        final List<String> quoteLines = <String>[];
        while (i < lines.length && lines[i].trim().startsWith('>')) {
          quoteLines.add(lines[i].trim().replaceFirst(RegExp(r'^>\s?'), ''));
          i++;
        }
        blocks.add(
          Container(
            width: double.infinity,
            margin: const EdgeInsets.symmetric(vertical: 3),
            padding: const EdgeInsets.only(left: 10, top: 2, bottom: 2),
            decoration: const BoxDecoration(
              border: Border(
                left: BorderSide(color: Color(0xFF388BFD), width: 2.5),
              ),
            ),
            child: InlineMarkdownText(
              text: quoteLines.join('\n'),
              fontSize: fontSize,
              color: const Color(0xFFB1BAC4),
            ),
          ),
        );
        continue;
      }

      if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
        blocks.add(
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 2),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Padding(
                  padding: const EdgeInsets.only(top: 2, right: 6),
                  child: Text(
                    '•',
                    style: TextStyle(
                      fontSize: fontSize,
                      color: const Color(0xFF58A6FF),
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
                Expanded(
                  child: InlineMarkdownText(
                    text: trimmed.substring(2),
                    fontSize: fontSize,
                    color: color,
                  ),
                ),
              ],
            ),
          ),
        );
        i++;
        continue;
      }

      blocks.add(
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 2),
          child: InlineMarkdownText(
            text: raw,
            fontSize: fontSize,
            color: color,
          ),
        ),
      );
      i++;
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: blocks,
    );
  }
}

class InlineMarkdownText extends StatelessWidget {
  const InlineMarkdownText({
    super.key,
    required this.text,
    this.fontSize = 12.5,
    this.color = const Color(0xFFE6EDF3),
    this.maxLines,
  });

  final String text;
  final double fontSize;
  final Color color;
  final int? maxLines;

  @override
  Widget build(BuildContext context) {
    final List<InlineSpan> spans = <InlineSpan>[];
    final RegExp tokenRe = RegExp(
      r'(`[^`]+`|\*\*[^*]+\*\*|\*[^*\n]+\*)',
    );
    int cursor = 0;
    for (final RegExpMatch match in tokenRe.allMatches(text)) {
      if (match.start > cursor) {
        spans.add(TextSpan(text: text.substring(cursor, match.start)));
      }
      final String tok = match.group(0)!;
      if (tok.startsWith('`') && tok.endsWith('`')) {
        spans.add(
          WidgetSpan(
            alignment: PlaceholderAlignment.middle,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 4.5, vertical: 1),
              margin: const EdgeInsets.symmetric(horizontal: 1),
              decoration: BoxDecoration(
                color: const Color(0xFF21262D),
                borderRadius: BorderRadius.circular(4),
                border: Border.all(color: const Color(0xFF30363D)),
              ),
              child: Text(
                tok.substring(1, tok.length - 1),
                style: TextStyle(
                  fontFamily: 'monospace',
                  fontSize: fontSize - 1,
                  color: const Color(0xFF79C0FF),
                ),
              ),
            ),
          ),
        );
      } else if (tok.startsWith('**') && tok.endsWith('**')) {
        spans.add(
          TextSpan(
            text: tok.substring(2, tok.length - 2),
            style: const TextStyle(fontWeight: FontWeight.w700),
          ),
        );
      } else if (tok.startsWith('*') && tok.endsWith('*')) {
        spans.add(
          TextSpan(
            text: tok.substring(1, tok.length - 1),
            style: const TextStyle(fontStyle: FontStyle.italic),
          ),
        );
      }
      cursor = match.end;
    }
    if (cursor < text.length) {
      spans.add(TextSpan(text: text.substring(cursor)));
    }

    final TextStyle baseStyle = TextStyle(
      fontSize: fontSize,
      height: 1.45,
      color: color,
    );

    return Text.rich(
      TextSpan(style: baseStyle, children: spans),
      maxLines: maxLines,
      overflow: maxLines != null ? TextOverflow.ellipsis : TextOverflow.clip,
    );
  }
}
