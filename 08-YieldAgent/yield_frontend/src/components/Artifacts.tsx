import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Download, FileText } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import type { CanvasCard } from "@/types";

// 아티팩트 종류(title)에 맞춰 iframe 높이를 결정 — app.py 의 _get_iframe_height 를 그대로 포팅.
// HTML 내부 <tr> 개수로 표 높이를 자동 보정. (의미 판단이 아닌 기계적 렌더 보조)
function iframeHeight(title: string, data: string): number {
  const t = (title || "").toLowerCase();
  const trCount = data ? (data.match(/<tr\b/g) || []).length : 0;
  if (t.includes("yield_table")) {
    return trCount > 0 ? Math.max(240, 270 + (trCount - 7) * 26) : 270;
  }
  if (t.includes("lot_compare")) {
    return trCount > 0 ? Math.max(260, 310 + (trCount - 5) * 26) : 310;
  }
  if (t.includes("cummap")) return 480;
  if (t.includes("scatter") || t.includes("trend")) return 1140;
  if (t.includes("fail_history") || t.includes("wads_report")) {
    if (!data) return 420;
    const hasSummary = data.includes("details") || data.includes("summary-block");
    const base = hasSummary ? 420 : 280;
    return trCount > 0 ? Math.max(220, base + (trCount - 4) * 35) : 420;
  }
  if (t.includes("relation_tree")) return 450;
  return 600;
}

// 콘텐츠는 자사 백엔드 출력(신뢰) → same-origin+scripts 허용(plotly 등 동작).
function HtmlArtifact({ title, data }: { title: string; data: string }) {
  return (
    <iframe
      title={title || "html-artifact"}
      srcDoc={data}
      sandbox="allow-same-origin allow-scripts allow-popups allow-popups-to-escape-sandbox"
      className="w-full rounded-md border bg-white"
      style={{ height: iframeHeight(title, data) }}
    />
  );
}

function PptxArtifact({ data, title }: { data: string; title: string }) {
  const fname = data.includes("/") ? data.split("/").pop() : data;
  return (
    <a
      href={data}
      download
      className="flex items-center gap-3 rounded-lg border bg-muted/60 px-3 py-2.5 transition-colors hover:border-[var(--warn)]/60"
    >
      <FileText className="size-7 text-[var(--warn)]" />
      <span className="flex flex-1 flex-col">
        <b className="text-sm">{title || "PPT 리포트"}</b>
        <small className="text-xs text-muted-foreground">{fname} · PPTX 다운로드</small>
      </span>
      <Download className="size-5 text-[var(--warn)]" />
    </a>
  );
}

// 단일 아티팩트 (카드 박스 없이 full-width 로 흐르게 — Streamlit 식)
function OneArtifact({ card }: { card: CanvasCard }) {
  return (
    <div>
      {card.title && (
        <div className="mb-1 font-mono text-[0.7rem] uppercase tracking-wide text-muted-foreground">
          {card.title}
        </div>
      )}
      {card.artifactType === "html" && <HtmlArtifact title={card.title} data={card.data} />}
      {card.artifactType === "markdown" && (
        <div className="overflow-x-auto text-sm leading-relaxed [&_h3]:mt-3 [&_h3]:font-semibold [&_table]:my-2 [&_table]:w-full [&_th]:border-b [&_th]:border-border [&_th]:py-1 [&_th]:text-left [&_td]:border-t [&_td]:border-border/60 [&_td]:py-1">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{card.data}</ReactMarkdown>
        </div>
      )}
      {card.artifactType === "image" && (
        <img src={card.data} alt={card.title} className="w-full rounded-md border" />
      )}
      {card.artifactType === "pptx" && <PptxArtifact data={card.data} title={card.title} />}
    </div>
  );
}

// 우측 패널: 아티팩트를 생성된 시간순 그대로, 연속된 같은 agent 구간만 한 카드로 묶어 그린다.
// (같은 agent를 전부 한 그룹으로 합치면 yield→wads→yield 가 yield-yield-wads 로 재정렬돼 wads가
//  맨 끝으로 밀린다. 연속 구간 그룹핑은 시간순을 보존하고 재등장 agent는 새 그룹이 된다.)
export function ArtifactPanel({ cards }: { cards: CanvasCard[] }) {
  const groups: { agent: string; items: CanvasCard[] }[] = [];
  for (const c of cards) {
    const last = groups[groups.length - 1];
    if (last && last.agent === c.agent) last.items.push(c);
    else groups.push({ agent: c.agent, items: [c] });
  }
  return (
    <div className="flex flex-col gap-4">
      {groups.map((g) => (
        <Card key={g.items[0].id} className="animate-[rise_0.32s_ease_both] overflow-hidden">
          <CardHeader className="border-b bg-muted/50">
            <Badge variant="default">{g.agent || "agent"}</Badge>
            <span className="text-[0.7rem] text-muted-foreground">{g.items.length}개 아티팩트</span>
          </CardHeader>
          <CardContent className="flex flex-col gap-4 pt-4">
            {g.items.map((c) => (
              <OneArtifact key={c.id} card={c} />
            ))}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
