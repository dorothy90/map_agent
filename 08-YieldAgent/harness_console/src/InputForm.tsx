import { useState } from 'react';
import { MessageSquare } from 'lucide-react';
import type { Question } from './types';
export function InputForm({question,onSubmit}: {question:Question;onSubmit:(value:string | Record<string,string>) => Promise<void>}) {
  const [values,setValues] = useState<Record<string,string>>({});const [reply,setReply] = useState('');const [busy,setBusy] = useState(false);const [error,setError] = useState('');
  const fields = question.fields || [];
  return <form className="input-request" onSubmit={async event => {
    event.preventDefault();setError('');
    const groups = [...new Set(fields.map(f => f.required_any_group).filter(Boolean))];
    if (groups.some(group => !fields.some(f => f.required_any_group === group && values[f.slot]?.trim()))) {setError('같은 선택 그룹에서 하나 이상 입력해주세요.');return;}
    setBusy(true);try {await onSubmit(fields.length ? Object.fromEntries(Object.entries(values).filter(([,v]) => v.trim())) : reply);} catch(cause) {setError(String(cause));} finally {setBusy(false);}
  }}><h3><MessageSquare size={16}/>추가 정보가 필요합니다</h3><p>{question.message || question.answer || '계속하려면 아래 정보를 입력해주세요.'}</p>
    {fields.length ? fields.map(field => <label key={field.slot}>{field.label || field.slot}{field.required_any_group && <small> · {field.required_any_group} 중 하나</small>}{field.options?.length ? <select value={values[field.slot] || ''} required={field.required !== false && !field.required_any_group} onChange={e => setValues(v => ({...v,[field.slot]:e.target.value}))}><option value="">선택하세요</option>{field.options.map(option => <option key={typeof option === 'string' ? option : option.value} value={typeof option === 'string' ? option : option.value}>{typeof option === 'string' ? option : option.label}</option>)}</select> : <input value={values[field.slot] || ''} required={field.required !== false && !field.required_any_group} onChange={e => setValues(v => ({...v,[field.slot]:e.target.value}))} placeholder={field.validation_hint || ''}/>}{field.validation_hint && <small>{field.validation_hint}</small>}</label>) : <label>답변<textarea required value={reply} onChange={e => setReply(e.target.value)}/></label>}
    {error && <p role="alert">{error}</p>}<button className="primary" disabled={busy}>{busy ? '전송 중…' : '답변하고 계속하기'}</button>
  </form>;
}
