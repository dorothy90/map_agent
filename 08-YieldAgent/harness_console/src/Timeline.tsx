import { Check, ChevronRight, Circle, Code2, ShieldCheck, Wrench } from 'lucide-react';
import { statusLabel, text, active } from './core';
import { Python } from './Markdown';
import type { Tool } from './types';
function ToolNode({tool,runStatus}: {tool:Tool;runStatus:string}) {
  const verification = ['verify_answer','completion_review'].includes(tool.name);
  const status = tool.status === 'running' && !active(runStatus) ? '미완료' : statusLabel(tool.status);
  const code = tool.name === 'run_python' ? text(tool.args?.code) : '';
  return <li><details className="tool"><summary><ChevronRight className="chevron" size={14}/>{verification ? <ShieldCheck size={16}/> : tool.name === 'run_python' ? <Code2 size={16}/> : <Wrench size={16}/>}<span className="tool-name">{tool.name}</span><span className={`tool-state state-${tool.status}`}>{tool.status === 'success' ? <Check size={12}/> : <Circle size={8}/>} {status}</span><span className="duration">{tool.elapsed ? `${tool.elapsed.toFixed(1)}s` : '—'}</span></summary>
    <div className="tool-detail">{tool.summary && <p>{tool.summary}</p>}
      {tool.args ? <>{code && <Python code={code}/>}<details><summary>입력 인자</summary><pre>{JSON.stringify(tool.args,null,2)}</pre></details></> : <p className="muted">저장된 실행 결과가 도착하면 입력 인자를 표시합니다.</p>}
      {tool.observation?.error && <pre className="error">{text(tool.observation.error)}</pre>}
      {verification && tool.observation?.scope && <pre>{text(tool.observation.scope)}</pre>}
    </div></details>{tool.children.length > 0 && <ol className="tool-children">{tool.children.map(child => <ToolNode key={child.id} tool={child} runStatus={runStatus}/>)}</ol>}</li>;
}
export function Timeline({tools,runStatus}: {tools:Tool[];runStatus:string}) {
  function count(nodes:Tool[]):number {return nodes.reduce((n,node) => n+1+count(node.children),0);}
  if (!tools.length) return null;
  return <details className="timeline" data-testid="tool-timeline" open><summary><span><Wrench size={15}/>도구 실행</span><span className="muted">{count(tools)}개 <ChevronRight size={14}/></span></summary><ol>{tools.map(tool => <ToolNode key={tool.id} tool={tool} runStatus={runStatus}/>)}</ol></details>;
}
