import { useState } from "react";
import { AlertTriangle, Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { HitlOption, InterruptPayload } from "@/types";

type ResumeValue = string | Record<string, unknown>;

// 선택지가 문자열("예") 또는 dict({value,label,hint}) 둘 다일 수 있다 → 정규화.
const optValue = (o: HitlOption) => (typeof o === "string" ? o : String(o.value ?? o.label ?? ""));
const optLabel = (o: HitlOption) => (typeof o === "string" ? o : String(o.label ?? o.value ?? ""));
const optHint = (o: HitlOption) => (typeof o === "string" ? undefined : o.hint);

interface Props {
  payload: InterruptPayload;
  answered?: string;
  busy: boolean;
  onResume: (value: ResumeValue, label: string) => void;
}

// ── plan_review: 계획 확인 — 그대로 실행 / 자유텍스트 수정 / 취소 (resume=str) ──
function PlanReview({ busy, onResume }: Omit<Props, "answered" | "payload">) {
  const [text, setText] = useState("");
  return (
    <>
      <div className="mb-2.5 flex items-start gap-2 text-sm text-[var(--warn)]">
        <AlertTriangle className="mt-0.5 size-4 shrink-0" />
        분석 계획 확인 — 그대로 진행하거나 수정 지시를 입력하세요.
      </div>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={busy} onClick={() => onResume("그대로 진행", "그대로 진행")}>
          <Check className="size-4" /> 그대로 실행
        </Button>
        <Button variant="outline" size="sm" disabled={busy} onClick={() => onResume("취소", "취소")}>
          <X className="size-4" /> 취소
        </Button>
      </div>
      <form
        className="mt-2.5 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          const t = text.trim();
          if (!t) return;
          onResume(t, t);
        }}
      >
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={busy}
          placeholder="수정 지시 — 예: WADS 기간 1주일로"
          className="h-9 flex-1 rounded-lg border bg-card px-3 text-sm outline-none focus:border-primary disabled:opacity-50"
        />
        <Button type="submit" variant="secondary" size="sm" disabled={busy || !text.trim()}>
          수정 요청
        </Button>
      </form>
    </>
  );
}

// ── missing_param: fields 폼(+options 빠른선택) → {slot: value} dict 로 resume ──
function MissingParam({ payload, busy, onResume }: Omit<Props, "answered">) {
  const [vals, setVals] = useState<Record<string, string>>({});
  const fields = payload.fields ?? [];
  const options = payload.options ?? [];

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const dict: Record<string, string> = {};
    for (const [k, v] of Object.entries(vals)) {
      if (v.trim()) dict[k] = v.trim();
    }
    if (Object.keys(dict).length === 0) return;
    const label = Object.entries(dict).map(([k, v]) => `${k}=${v}`).join(", ");
    onResume(dict, label);
  }

  return (
    <>
      <div className="mb-2.5 flex items-start gap-2 text-sm text-[var(--warn)]">
        <AlertTriangle className="mt-0.5 size-4 shrink-0" />
        {payload.message || "추가 정보가 필요합니다."}
      </div>

      {/* 상위 options (예: yield_wads_followup 예/아니오) — 클릭 즉시 resume */}
      {options.length > 0 && (
        <div className="mb-2.5 flex flex-wrap gap-2">
          {options.map((o, i) => {
            const value = optValue(o);
            const label = optLabel(o);
            const hint = optHint(o);
            const param = payload.param;
            return (
              <button
                key={i}
                disabled={busy}
                onClick={() => onResume(param ? { [param]: value } : value, label)}
                className="flex min-w-[140px] flex-1 flex-col gap-0.5 rounded-lg border bg-card px-3 py-2 text-left transition-colors hover:border-[var(--warn)] hover:bg-[var(--warn)]/10 disabled:opacity-50"
              >
                <b className="text-sm">{label}</b>
                {hint && <small className="text-xs text-muted-foreground">{hint}</small>}
              </button>
            );
          })}
        </div>
      )}

      {/* fields 폼 — choice(모호 슬롯)는 후보 버튼, 그 외는 자유입력 → {slot: value} dict */}
      {fields.length > 0 && (
        <form className="flex flex-col gap-2" onSubmit={submit}>
          {fields.map((f) => (
            <label key={f.slot} className="flex flex-col gap-1 text-xs text-muted-foreground">
              <span>
                {f.label || f.slot}
                {f.required_any_group && (
                  <span className="ml-1 text-[var(--warn)]">· {f.required_any_group} 중 택1</span>
                )}
              </span>
              {f.options && f.options.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {f.options.map((o, i) => {
                    const value = optValue(o);
                    const selected = (vals[f.slot] ?? "") === value;
                    return (
                      <button
                        key={i}
                        type="button"
                        disabled={busy}
                        onClick={() => setVals((v) => ({ ...v, [f.slot]: value }))}
                        className={cn(
                          "rounded-lg border px-3 py-1.5 text-sm text-foreground transition-colors disabled:opacity-50",
                          selected
                            ? "border-[var(--warn)] bg-[var(--warn)]/15"
                            : "bg-card hover:border-[var(--warn)] hover:bg-[var(--warn)]/10",
                        )}
                      >
                        {optLabel(o)}
                      </button>
                    );
                  })}
                </div>
              ) : (
                <input
                  value={vals[f.slot] ?? ""}
                  onChange={(e) => setVals((v) => ({ ...v, [f.slot]: e.target.value }))}
                  disabled={busy}
                  placeholder={f.validation_hint || f.slot}
                  className="h-9 rounded-lg border bg-card px-3 text-sm text-foreground outline-none focus:border-primary disabled:opacity-50"
                />
              )}
            </label>
          ))}
          <Button type="submit" size="sm" disabled={busy} className="self-start">
            제출
          </Button>
        </form>
      )}
    </>
  );
}

export function HitlCard({ payload, answered, busy, onResume }: Props) {
  const isPlanReview = payload.interrupt_type === "plan_review";
  return (
    <div className="self-stretch rounded-xl border border-[var(--warn)]/40 bg-[var(--warn)]/[0.06] p-3">
      {answered ? (
        <div className="flex items-center gap-2 text-sm text-[var(--good)]">
          <Check className="size-4" /> 응답: {answered}
        </div>
      ) : isPlanReview ? (
        <PlanReview busy={busy} onResume={onResume} />
      ) : (
        <MissingParam payload={payload} busy={busy} onResume={onResume} />
      )}
    </div>
  );
}
