(function () {
  'use strict';

  const RESERVED_IDS = new Set([
    'end',
    'subgraph',
    'graph',
    'flowchart',
    'class',
    'classdef',
    'style',
    'linkstyle',
    'click',
    'call',
    'default',
    'direction',
  ]);

  function cleanLabelText(raw) {
    if (!raw) return '';
    return String(raw)
      .replace(/\\n|\r?\n/g, ' - ')
      .replace(/<\s*br\s*\/?\s*>/gi, ' - ')
      .replace(/<([A-Za-z0-9_., /+-]+)>/g, '$1')
      .replace(/[<>]/g, '')
      .replace(/^["']+|["']+$/g, '')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function wrapLines(text, maxChars) {
    const words = String(text || '').split(/\s+/).filter(Boolean);
    if (words.length === 0) return [''];
    const lines = [];
    let current = words[0];
    for (let i = 1; i < words.length; i++) {
      const w = words[i];
      if ((current + ' ' + w).length <= maxChars) {
        current += ' ' + w;
      } else {
        lines.push(current);
        current = w.length > maxChars ? w.slice(0, maxChars - 1) + '…' : w;
      }
    }
    if (current) lines.push(current);
    return lines.slice(0, 4);
  }

  function splitNodeTitleAndLines(label) {
    const cleaned = cleanLabelText(label);
    const segments = cleaned
      .split(/\s+-\s+|\s+—\s+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (segments.length === 0) {
      return { titleLines: ['Node'], subLines: [] };
    }
    const primary = segments[0].replace(/\s*\([^)]{22,}\)\s*$/, '').trim() || segments[0];
    const titleLines = wrapLines(primary, 26).slice(0, 2);
    const subLines = [];
    if (segments.length > 1) {
      const second = segments[1].replace(/^\((.*)\)$/, '$1').trim();
      if (second) {
        const shortSub = second.length > 32 ? second.slice(0, 31) + '…' : second;
        subLines.push(shortSub);
      }
    }
    return { titleLines, subLines };
  }

  function parseMermaidFlowchart(source, overrideDir) {
    const raw = String(source || '')
      .replace(/^```(?:mermaid)?\s*\r?\n?/i, '')
      .replace(/\r?\n?```\s*$/i, '')
      .replace(/"([^"]*)"/g, (_, inner) => '"' + inner.replace(/\s*\r?\n\s*/g, ' - ').trim() + '"')
      .trim();

    const lines = raw
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter((l) => l && !l.startsWith('%%'));

    let direction = 'LR';
    let startIdx = 0;
    if (lines.length > 0 && /^(?:flowchart|graph)\b/i.test(lines[0])) {
      const parts = lines[0].split(/\s+/);
      if (parts[1] && /^(TD|TB|BT|RL|LR)$/i.test(parts[1])) {
        const d = parts[1].toUpperCase();
        direction = d === 'TB' || d === 'TD' || d === 'BT' ? 'TD' : 'LR';
      }
      startIdx = 1;
    }
    if (overrideDir === 'LR' || overrideDir === 'TD') {
      direction = overrideDir;
    }

    const nodesMap = new Map();
    const subgraphs = [];
    const edges = [];
    const subgraphStack = [];

    function ensureNode(id, label, sgId) {
      const cleanId = String(id || '').replace(/[^A-Za-z0-9_]/g, '_');
      if (!cleanId || RESERVED_IDS.has(cleanId.toLowerCase())) return null;
      const activeSg = sgId !== undefined ? sgId : subgraphStack[subgraphStack.length - 1] || null;
      if (!nodesMap.has(cleanId)) {
        nodesMap.set(cleanId, {
          id: cleanId,
          label: cleanLabelText(label || cleanId),
          subgraphId: activeSg,
        });
      } else {
        const existing = nodesMap.get(cleanId);
        if (label && existing.label === existing.id) {
          existing.label = cleanLabelText(label);
        }
        if (!existing.subgraphId && activeSg) {
          existing.subgraphId = activeSg;
        }
      }
      return cleanId;
    }

    function extractNodeToken(tokenStr) {
      const t = String(tokenStr || '').trim();
      if (!t) return null;
      const m =
        t.match(/^([A-Za-z0-9_]+)\s*\[\s*"([^"]*)"\s*\]$/) ||
        t.match(/^([A-Za-z0-9_]+)\s*\[\s*([^\]]+)\s*\]$/) ||
        t.match(/^([A-Za-z0-9_]+)\s*\(\s*"?([^")]*)"?\s*\)$/) ||
        t.match(/^([A-Za-z0-9_]+)\s*\{\s*"?([^"}]*)"?\s*\}$/);
      if (m) {
        return ensureNode(m[1], m[2]);
      }
      const mBare = t.match(/^([A-Za-z0-9_]+)$/);
      if (mBare) {
        return ensureNode(mBare[1], null);
      }
      return null;
    }

    for (let i = startIdx; i < lines.length; i++) {
      let line = lines[i];
      if (!line || /^(?:classDef|class|style|linkStyle|click|direction)\b/i.test(line)) {
        continue;
      }
      if (/^end$/i.test(line)) {
        subgraphStack.pop();
        continue;
      }
      const sgMatch = line.match(/^subgraph\s+(.+)$/i);
      if (sgMatch) {
        const rest = sgMatch[1].trim();
        const mBracket = rest.match(/^([A-Za-z0-9_]+)\s*\[\s*"?([^"\]]*)"?\s*\]$/);
        const mQuoted = rest.match(/^([A-Za-z0-9_]+)\s+"([^"]+)"$/);
        const mOnlyQuoted = rest.match(/^"([^"]+)"$/);
        let sgId = 'sg_' + (subgraphs.length + 1);
        let sgTitle = rest;
        if (mBracket) {
          sgId = mBracket[1];
          sgTitle = mBracket[2];
        } else if (mQuoted) {
          sgId = mQuoted[1];
          sgTitle = mQuoted[2];
        } else if (mOnlyQuoted) {
          sgTitle = mOnlyQuoted[1];
        }
        sgId = sgId.replace(/[^A-Za-z0-9_]/g, '_');
        subgraphs.push({
          id: sgId,
          title: cleanLabelText(sgTitle),
          parentId: subgraphStack[subgraphStack.length - 1] || null,
        });
        subgraphStack.push(sgId);
        continue;
      }

      // Normalize inline -- "label" --> to -->|"label"|
      line = line.replace(/--\s*"([^"]+)"\s*-->/g, '-->|"$1"|');
      line = line.replace(/-\.\s*"([^"]+)"\s*\.->/g, '-.->|"$1"|');

      // Check if line contains arrows
      const arrowRegex =
        /\s*(<-->|<-\.->|<==>|-\.->|==>|--+>)\s*(?:\|\s*"?([^"|]*)"?\s*\|)?\s*/g;
      if (arrowRegex.test(line)) {
        arrowRegex.lastIndex = 0;
        const segments = [];
        const arrows = [];
        let lastIndex = 0;
        let m;
        while ((m = arrowRegex.exec(line)) !== null) {
          segments.push(line.slice(lastIndex, m.index).trim());
          arrows.push({
            rawArrow: m[1],
            label: cleanLabelText(m[2] || ''),
          });
          lastIndex = arrowRegex.lastIndex;
        }
        segments.push(line.slice(lastIndex).trim());

        for (let k = 0; k < arrows.length; k++) {
          const fromId = extractNodeToken(segments[k]);
          const toId = extractNodeToken(segments[k + 1]);
          if (fromId && toId) {
            const rawA = arrows[k].rawArrow;
            edges.push({
              from: fromId,
              to: toId,
              label: arrows[k].label,
              dotted: rawA.includes('.'),
              thick: rawA.includes('='),
              bi: rawA.startsWith('<'),
            });
          }
        }
      } else {
        extractNodeToken(line);
      }
    }

    return {
      direction,
      nodes: Array.from(nodesMap.values()),
      subgraphs,
      edges,
    };
  }

  function layoutFlowchart(parsed) {
    const { direction, nodes, subgraphs, edges } = parsed;
    const isLR = direction === 'LR';

    // Measure each node's box dimensions
    const nodeMeta = new Map();
    for (const n of nodes) {
      const { titleLines, subLines } = splitNodeTitleAndLines(n.label);
      let maxLen = 10;
      for (const l of titleLines) maxLen = Math.max(maxLen, l.length);
      for (const l of subLines) maxLen = Math.max(maxLen, Math.ceil(l.length * 0.9));
      const width = Math.max(168, Math.min(285, maxLen * 7.6 + 34));
      const height =
        24 +
        titleLines.length * 18 +
        (subLines.length > 0 ? 6 + subLines.length * 15 : 0);
      nodeMeta.set(n.id, {
        ...n,
        titleLines,
        subLines,
        width,
        height,
        rank: 0,
        order: 0,
        x: 0,
        y: 0,
      });
    }

    // Assign topological ranks (Longest-path DAG layering with cycle guard)
    const adj = new Map();
    const indeg = new Map();
    for (const n of nodes) {
      adj.set(n.id, []);
      indeg.set(n.id, 0);
    }
    for (const e of edges) {
      if (e.from !== e.to && adj.has(e.from) && adj.has(e.to)) {
        adj.get(e.from).push(e.to);
        indeg.set(e.to, (indeg.get(e.to) || 0) + 1);
      }
    }

    // If subgraphs exist and edges go between subgraphs, keep subgraph nodes grouped when possible
    const visited = new Set();
    const queue = [];
    for (const n of nodes) {
      if ((indeg.get(n.id) || 0) === 0) {
        queue.push(n.id);
      }
    }
    if (queue.length === 0 && nodes.length > 0) {
      queue.push(nodes[0].id);
    }

    while (queue.length > 0) {
      const curr = queue.shift();
      visited.add(curr);
      const currRank = nodeMeta.get(curr).rank;
      for (const nxt of adj.get(curr) || []) {
        const nxtMeta = nodeMeta.get(nxt);
        if (!visited.has(nxt)) {
          nxtMeta.rank = Math.max(nxtMeta.rank, currRank + 1);
        }
        indeg.set(nxt, (indeg.get(nxt) || 1) - 1);
        if (indeg.get(nxt) <= 0 && !visited.has(nxt) && !queue.includes(nxt)) {
          queue.push(nxt);
        }
      }
      if (queue.length === 0 && visited.size < nodes.length) {
        const remaining = nodes.find((n) => !visited.has(n.id));
        if (remaining) queue.push(remaining.id);
      }
    }

    // Group by rank (or by subgraph order if subgraphs are prominent)
    const layersMap = new Map();
    for (const n of nodes) {
      const m = nodeMeta.get(n.id);
      if (!layersMap.has(m.rank)) layersMap.set(m.rank, []);
      layersMap.get(m.rank).push(m);
    }

    const sortedRanks = Array.from(layersMap.keys()).sort((a, b) => a - b);
    const sgIndexOrder = new Map();
    subgraphs.forEach((sg, idx) => sgIndexOrder.set(sg.id, idx + 1));

    // Barycenter + subgraph ordering within each rank
    for (let rIdx = 0; rIdx < sortedRanks.length; rIdx++) {
      const rank = sortedRanks[rIdx];
      const layer = layersMap.get(rank);
      layer.sort((a, b) => {
        const sgA = sgIndexOrder.get(a.subgraphId) || 999;
        const sgB = sgIndexOrder.get(b.subgraphId) || 999;
        if (sgA !== sgB) return sgA - sgB;
        return 0;
      });
      layer.forEach((m, idx) => {
        m.order = idx;
      });
    }

    // Compute coordinates
    const hasEdgeLabels = edges.some((e) => Boolean(e.label));
    const rankGap = isLR ? (hasEdgeLabels ? 118 : 78) : hasEdgeLabels ? 92 : 68;
    const nodeGap = isLR ? 34 : 38;
    const padX = 44;
    const padY = subgraphs.length > 0 ? 58 : 36;

    // Determine max secondary span across all layers for centering
    const layerSizes = sortedRanks.map((rank) => {
      const layer = layersMap.get(rank);
      let primarySpan = 0;
      let secondaryTotal = 0;
      for (const m of layer) {
        const pSize = isLR ? m.width : m.height;
        const sSize = isLR ? m.height : m.width;
        primarySpan = Math.max(primarySpan, pSize);
        secondaryTotal += sSize;
      }
      secondaryTotal += Math.max(0, layer.length - 1) * nodeGap;
      return { rank, layer, primarySpan, secondaryTotal };
    });

    const maxSecondary = Math.max(240, ...layerSizes.map((l) => l.secondaryTotal));
    let primaryCursor = padX;

    for (const lInfo of layerSizes) {
      let secondaryCursor = padY + (maxSecondary - lInfo.secondaryTotal) / 2;
      for (const m of lInfo.layer) {
        if (isLR) {
          m.x = primaryCursor + (lInfo.primarySpan - m.width) / 2;
          m.y = secondaryCursor;
          secondaryCursor += m.height + nodeGap;
        } else {
          m.x = secondaryCursor;
          m.y = primaryCursor + (lInfo.primarySpan - m.height) / 2;
          secondaryCursor += m.width + nodeGap;
        }
      }
      primaryCursor += lInfo.primarySpan + rankGap;
    }

    // Compute subgraph bounding boxes
    const subgraphBoxes = [];
    for (const sg of subgraphs) {
      const members = Array.from(nodeMeta.values()).filter((m) => m.subgraphId === sg.id);
      if (members.length === 0) continue;
      const minX = Math.min(...members.map((m) => m.x)) - 18;
      const minY = Math.min(...members.map((m) => m.y)) - 34;
      const maxX = Math.max(...members.map((m) => m.x + m.width)) + 18;
      const maxY = Math.max(...members.map((m) => m.y + m.height)) + 16;
      subgraphBoxes.push({
        id: sg.id,
        title: sg.title,
        x: minX,
        y: minY,
        width: maxX - minX,
        height: maxY - minY,
      });
    }

    const allNodes = Array.from(nodeMeta.values());
    const minCanvasX = Math.min(
      0,
      ...allNodes.map((n) => n.x - 24),
      ...subgraphBoxes.map((s) => s.x - 24)
    );
    const minCanvasY = Math.min(
      0,
      ...allNodes.map((n) => n.y - 24),
      ...subgraphBoxes.map((s) => s.y - 24)
    );
    if (minCanvasX < 0 || minCanvasY < 0) {
      const dx = minCanvasX < 0 ? -minCanvasX : 0;
      const dy = minCanvasY < 0 ? -minCanvasY : 0;
      for (const n of allNodes) {
        n.x += dx;
        n.y += dy;
      }
      for (const s of subgraphBoxes) {
        s.x += dx;
        s.y += dy;
      }
    }

    const totalWidth = Math.max(
      420,
      ...allNodes.map((n) => n.x + n.width + 44),
      ...subgraphBoxes.map((s) => s.x + s.width + 44)
    );
    const totalHeight = Math.max(
      220,
      ...allNodes.map((n) => n.y + n.height + 40),
      ...subgraphBoxes.map((s) => s.y + s.height + 40)
    );

    return {
      isLR,
      direction,
      nodes: allNodes,
      nodeMeta,
      subgraphBoxes,
      edges,
      totalWidth,
      totalHeight,
    };
  }

  function escapeXml(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function renderSvgFlowchart(source, options) {
    const opts = options || {};
    const parsed = parseMermaidFlowchart(source, opts.direction);
    if (!parsed.nodes || parsed.nodes.length === 0) {
      return null;
    }
    const layout = layoutFlowchart(parsed);
    const { isLR, nodes, nodeMeta, subgraphBoxes, edges, totalWidth, totalHeight } = layout;

    const svgParts = [];
    svgParts.push(
      `<svg class="quasar-flowchart-svg" viewBox="0 0 ${Math.ceil(totalWidth)} ${Math.ceil(
        totalHeight
      )}" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg">`
    );
    svgParts.push(`
      <defs>
        <marker id="q-arrow" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">
          <path d="M 0 1.5 L 9 5 L 0 8.5 z" fill="var(--q-edge-color, #6b9eff)" />
        </marker>
        <filter id="q-shadow" x="-8%" y="-8%" width="116%" height="120%">
          <feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="#000000" flood-opacity="0.22" />
        </filter>
      </defs>
    `);

    // 1. Subgraph containers
    for (let i = 0; i < subgraphBoxes.length; i++) {
      const sg = subgraphBoxes[i];
      svgParts.push(`
        <g class="q-subgraph">
          <rect x="${sg.x.toFixed(1)}" y="${sg.y.toFixed(1)}" width="${sg.width.toFixed(
        1
      )}" height="${sg.height.toFixed(
        1
      )}" rx="10" ry="10" fill="var(--q-sg-bg, rgba(128,128,128,0.07))" stroke="var(--q-sg-border, rgba(128,128,128,0.28))" stroke-width="1.2" stroke-dasharray="5,3" />
          <text x="${(sg.x + 12).toFixed(1)}" y="${(sg.y + 19).toFixed(
        1
      )}" fill="var(--q-sg-text, #8b949e)" font-size="11.5" font-weight="600" font-family="var(--vscode-font-family, system-ui, sans-serif)">${escapeXml(
        sg.title
      )}</text>
        </g>
      `);
    }

    // 2. Edges + Edge Labels
    const edgeLabelElements = [];
    for (let i = 0; i < edges.length; i++) {
      const e = edges[i];
      const u = nodeMeta.get(e.from);
      const v = nodeMeta.get(e.to);
      if (!u || !v) continue;

      let x1, y1, x2, y2, cx1, cy1, cx2, cy2;
      if (isLR) {
        if (u.x + u.width <= v.x + 10) {
          x1 = u.x + u.width;
          y1 = u.y + u.height / 2;
          x2 = v.x;
          y2 = v.y + v.height / 2;
          const dx = Math.max(32, (x2 - x1) * 0.48);
          cx1 = x1 + dx;
          cy1 = y1;
          cx2 = x2 - dx;
          cy2 = y2;
        } else if (v.x + v.width <= u.x + 10) {
          x1 = u.x;
          y1 = u.y + u.height / 2;
          x2 = v.x + v.width;
          y2 = v.y + v.height / 2;
          const dx = Math.max(32, (x1 - x2) * 0.48);
          cx1 = x1 - dx;
          cy1 = y1;
          cx2 = x2 + dx;
          cy2 = y2;
        } else {
          const down = u.y <= v.y;
          x1 = u.x + u.width / 2;
          y1 = down ? u.y + u.height : u.y;
          x2 = v.x + v.width / 2;
          y2 = down ? v.y : v.y + v.height;
          const dy = Math.max(24, Math.abs(y2 - y1) * 0.45);
          cx1 = x1;
          cy1 = down ? y1 + dy : y1 - dy;
          cx2 = x2;
          cy2 = down ? y2 - dy : y2 + dy;
        }
      } else {
        if (u.y + u.height <= v.y + 10) {
          x1 = u.x + u.width / 2;
          y1 = u.y + u.height;
          x2 = v.x + v.width / 2;
          y2 = v.y;
          const dy = Math.max(28, (y2 - y1) * 0.48);
          cx1 = x1;
          cy1 = y1 + dy;
          cx2 = x2;
          cy2 = y2 - dy;
        } else if (v.y + v.height <= u.y + 10) {
          x1 = u.x + u.width / 2;
          y1 = u.y;
          x2 = v.x + v.width / 2;
          y2 = v.y + v.height;
          const dy = Math.max(28, (y1 - y2) * 0.48);
          cx1 = x1;
          cy1 = y1 - dy;
          cx2 = x2;
          cy2 = y2 + dy;
        } else {
          const right = u.x <= v.x;
          x1 = right ? u.x + u.width : u.x;
          y1 = u.y + u.height / 2;
          x2 = right ? v.x : v.x + v.width;
          y2 = v.y + v.height / 2;
          const dx = Math.max(24, Math.abs(x2 - x1) * 0.45);
          cx1 = right ? x1 + dx : x1 - dx;
          cy1 = y1;
          cx2 = right ? x2 - dx : x2 + dx;
          cy2 = y2;
        }
      }

      const pathD = `M ${x1.toFixed(1)} ${y1.toFixed(1)} C ${cx1.toFixed(1)} ${cy1.toFixed(
        1
      )}, ${cx2.toFixed(1)} ${cy2.toFixed(1)}, ${x2.toFixed(1)} ${y2.toFixed(1)}`;
      const dashAttr = e.dotted ? 'stroke-dasharray="5,4"' : '';
      const strokeW = e.thick ? '2.4' : '1.65';
      const markerStart = e.bi ? 'marker-start="url(#q-arrow)"' : '';

      svgParts.push(
        `<path d="${pathD}" fill="none" stroke="var(--q-edge-color, #6b9eff)" stroke-width="${strokeW}" ${dashAttr} ${markerStart} marker-end="url(#q-arrow)" opacity="0.88" />`
      );

      if (e.label) {
        const mx = (x1 + cx1 + cx2 + x2) / 4;
        const my = (y1 + cy1 + cy2 + y2) / 4;
        const shortLbl = e.label.length > 36 ? e.label.slice(0, 35) + '…' : e.label;
        const pillW = Math.max(44, shortLbl.length * 6.2 + 16);
        const pillH = 20;
        edgeLabelElements.push(`
          <g class="q-edge-label" transform="translate(${(mx - pillW / 2).toFixed(1)}, ${(
          my -
          pillH / 2
        ).toFixed(1)})">
            <rect width="${pillW.toFixed(
              1
            )}" height="${pillH}" rx="10" ry="10" fill="var(--q-pill-bg, #1e2430)" stroke="var(--q-edge-color, #6b9eff)" stroke-width="1" />
            <text x="${(pillW / 2).toFixed(
              1
            )}" y="13.5" text-anchor="middle" fill="var(--q-pill-text, #c9d1d9)" font-size="10.5" font-family="var(--vscode-font-family, system-ui, sans-serif)">${escapeXml(
          shortLbl
        )}</text>
          </g>
        `);
      }
    }

    // 3. Nodes
    for (const n of nodes) {
      const matchTarget =
        typeof opts.resolveNodeTarget === 'function' ? opts.resolveNodeTarget(n) : null;
      const clickableClass = matchTarget ? 'q-node q-node-clickable' : 'q-node';
      const dataAttrs = matchTarget
        ? `data-stop-id="${escapeXml(matchTarget.stopId || '')}" data-path="${escapeXml(
            matchTarget.path || ''
          )}" data-line="${escapeXml(String(matchTarget.line || 1))}"`
        : `data-node-label="${escapeXml(n.label)}"`;
      const tooltipTitle = matchTarget
        ? `${n.label}\n\nClick to jump to ${matchTarget.badge || matchTarget.path} in Diff Editor`
        : `${n.label}\n\nClick to search Tour Stops for "${n.label}"`;

      svgParts.push(
        `<g class="${clickableClass}" ${dataAttrs} transform="translate(${n.x.toFixed(
          1
        )}, ${n.y.toFixed(1)})" style="cursor: pointer;">`
      );
      svgParts.push(`<title>${escapeXml(tooltipTitle)}</title>`);
      svgParts.push(
        `<rect class="q-node-box" width="${n.width.toFixed(1)}" height="${n.height.toFixed(
          1
        )}" rx="8" ry="8" fill="var(--q-node-bg, #1f2937)" stroke="${
          matchTarget
            ? 'var(--q-node-active-border, #58a6ff)'
            : 'var(--q-node-border, rgba(110,168,254,0.45))'
        }" stroke-width="${matchTarget ? '1.6' : '1.2'}" filter="url(#q-shadow)" />`
      );

      let textY = 21;
      for (const tLine of n.titleLines) {
        svgParts.push(
          `<text x="${(n.width / 2).toFixed(
            1
          )}" y="${textY}" text-anchor="middle" fill="var(--q-node-title, #f0f6fc)" font-size="12" font-weight="600" font-family="var(--vscode-font-family, system-ui, sans-serif)">${escapeXml(
            tLine
          )}</text>`
        );
        textY += 17;
      }
      if (n.subLines.length > 0) {
        svgParts.push(
          `<line x1="12" y1="${textY - 10}" x2="${(n.width - 12).toFixed(1)}" y2="${
            textY - 10
          }" stroke="var(--q-node-divider, rgba(140,150,165,0.22))" stroke-width="1" />`
        );
        textY += 3;
        for (const sLine of n.subLines) {
          svgParts.push(
            `<text x="${(n.width / 2).toFixed(
              1
            )}" y="${textY}" text-anchor="middle" fill="var(--q-node-sub, #9ca3af)" font-size="10.8" font-family="var(--vscode-font-family, system-ui, sans-serif)">${escapeXml(
              sLine
            )}</text>`
          );
          textY += 15;
        }
      }
      svgParts.push(`</g>`);
    }

    for (const el of edgeLabelElements) {
      svgParts.push(el);
    }

    svgParts.push(`</svg>`);
    return {
      svg: svgParts.join('\n'),
      direction: layout.direction,
      nodesCount: nodes.length,
      edgesCount: edges.length,
    };
  }

  function mountInteractiveFlowchart(container, source, options) {
    if (!container) return;
    let currentDir = null;
    let scale = 1;
    let panX = 0;
    let panY = 0;

    function render() {
      const res = renderSvgFlowchart(source, { ...(options || {}), direction: currentDir });
      if (!res) {
        container.innerHTML = `<pre class="quasar-mermaid-fallback"><code>${escapeXml(
          source
        )}</code></pre>`;
        return;
      }
      currentDir = res.direction;
      container.innerHTML = `
        <div class="quasar-fc-wrapper">
          <div class="quasar-fc-toolbar">
            <span class="quasar-fc-badge">🧭 Interactive Flowchart (${res.nodesCount} nodes • click any node to jump to diff)</span>
            <div class="quasar-fc-controls">
              <button type="button" class="q-fc-btn" data-fc-act="dir" title="Switch Horizontal (LR) / Vertical (TD) Layout">⇄ ${
                currentDir === 'LR' ? 'LR → TD' : 'TD → LR'
              }</button>
              <button type="button" class="q-fc-btn" data-fc-act="zoom-in" title="Zoom In">＋</button>
              <button type="button" class="q-fc-btn" data-fc-act="zoom-out" title="Zoom Out">－</button>
              <button type="button" class="q-fc-btn" data-fc-act="reset" title="Reset Zoom & Pan">⟲ Reset</button>
              <button type="button" class="q-fc-btn" data-fc-act="copy" title="Copy Mermaid Source">📋 Copy</button>
            </div>
          </div>
          <div class="quasar-fc-viewport">
            <div class="quasar-fc-stage" style="transform: translate(${panX}px, ${panY}px) scale(${scale}); transform-origin: center center;">
              ${res.svg}
            </div>
          </div>
        </div>
      `;

      const stage = container.querySelector('.quasar-fc-stage');
      const viewport = container.querySelector('.quasar-fc-viewport');
      function applyTransform() {
        if (stage) {
          stage.style.transform = `translate(${panX}px, ${panY}px) scale(${scale})`;
        }
      }

      container.querySelectorAll('.q-fc-btn').forEach((btn) => {
        btn.addEventListener('click', (ev) => {
          ev.stopPropagation();
          const act = btn.getAttribute('data-fc-act');
          if (act === 'zoom-in') {
            scale = Math.min(2.6, +(scale + 0.18).toFixed(2));
            applyTransform();
          } else if (act === 'zoom-out') {
            scale = Math.max(0.45, +(scale - 0.18).toFixed(2));
            applyTransform();
          } else if (act === 'reset') {
            scale = 1;
            panX = 0;
            panY = 0;
            applyTransform();
          } else if (act === 'dir') {
            currentDir = currentDir === 'LR' ? 'TD' : 'LR';
            scale = 1;
            panX = 0;
            panY = 0;
            render();
          } else if (act === 'copy') {
            if (navigator.clipboard && navigator.clipboard.writeText) {
              navigator.clipboard.writeText(source);
              btn.textContent = '✓ Copied';
              setTimeout(() => {
                btn.textContent = '📋 Copy';
              }, 1400);
            }
          }
        });
      });

      // Drag to pan
      let dragging = false;
      let startX = 0;
      let startY = 0;
      if (viewport) {
        viewport.addEventListener('mousedown', (ev) => {
          if (ev.target.closest('.q-node') || ev.target.closest('.q-fc-btn')) return;
          dragging = true;
          startX = ev.clientX - panX;
          startY = ev.clientY - panY;
          viewport.style.cursor = 'grabbing';
        });
        window.addEventListener('mousemove', (ev) => {
          if (!dragging) return;
          panX = ev.clientX - startX;
          panY = ev.clientY - startY;
          applyTransform();
        });
        window.addEventListener('mouseup', () => {
          if (dragging) {
            dragging = false;
            viewport.style.cursor = 'grab';
          }
        });
      }

      // Click on node
      container.querySelectorAll('.q-node').forEach((nodeEl) => {
        nodeEl.addEventListener('click', (ev) => {
          ev.stopPropagation();
          if (options && typeof options.onNodeClick === 'function') {
            options.onNodeClick({
              stopId: nodeEl.getAttribute('data-stop-id') || '',
              path: nodeEl.getAttribute('data-path') || '',
              line: parseInt(nodeEl.getAttribute('data-line') || '1', 10) || 1,
              label: nodeEl.getAttribute('data-node-label') || '',
            });
          }
        });
      });
    }

    render();
  }

  function injectMarkdownPreviewStyles() {
    if (document.getElementById('quasar-mermaid-preview-styles')) return;
    const style = document.createElement('style');
    style.id = 'quasar-mermaid-preview-styles';
    style.textContent = `
      .quasar-fc-wrapper {
        border: 1px solid rgba(128, 128, 128, 0.28);
        border-radius: 10px;
        background: rgba(128, 128, 128, 0.04);
        margin: 12px 0 18px 0;
        overflow: hidden;
      }
      .quasar-fc-toolbar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 7px 12px;
        border-bottom: 1px solid rgba(128, 128, 128, 0.2);
        background: rgba(128, 128, 128, 0.07);
        font-size: 11.5px;
      }
      .quasar-fc-badge {
        opacity: 0.85;
        font-weight: 600;
      }
      .quasar-fc-controls {
        display: flex;
        gap: 6px;
      }
      .q-fc-btn {
        background: rgba(128, 128, 128, 0.14);
        color: inherit;
        border: 1px solid rgba(128, 128, 128, 0.28);
        border-radius: 5px;
        padding: 2px 8px;
        font-size: 11px;
        cursor: pointer;
      }
      .q-fc-btn:hover {
        background: rgba(100, 160, 255, 0.22);
      }
      .quasar-fc-viewport {
        padding: 14px;
        overflow: hidden;
        cursor: grab;
      }
      .quasar-fc-stage {
        transition: transform 0.08s ease-out;
      }
      .q-node:hover .q-node-box {
        stroke: #58a6ff !important;
        stroke-width: 2px !important;
      }
    `;
    document.head.appendChild(style);
  }

  function enhanceMarkdownPreviewMermaidBlocks() {
    injectMarkdownPreviewStyles();
    const codeBlocks = document.querySelectorAll(
      'pre > code.language-mermaid, pre.mermaid, code.mermaid'
    );
    codeBlocks.forEach((codeEl) => {
      const pre = codeEl.tagName === 'PRE' ? codeEl : codeEl.closest('pre') || codeEl;
      if (pre.getAttribute('data-quasar-rendered') === '1') return;
      const rawSource = codeEl.textContent || '';
      if (!rawSource.trim()) return;
      pre.setAttribute('data-quasar-rendered', '1');
      const host = document.createElement('div');
      pre.parentNode.replaceChild(host, pre);
      mountInteractiveFlowchart(host, rawSource, {});
    });
  }

  window.QuasarFlowchartRenderer = {
    parseMermaidFlowchart,
    renderSvgFlowchart,
    mountInteractiveFlowchart,
    enhanceMarkdownPreviewMermaidBlocks,
  };

  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', enhanceMarkdownPreviewMermaidBlocks);
    } else {
      enhanceMarkdownPreviewMermaidBlocks();
    }
    window.addEventListener('vscode.markdown.updateContent', enhanceMarkdownPreviewMermaidBlocks);
  }
})();
