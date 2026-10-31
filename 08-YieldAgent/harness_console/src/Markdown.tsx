import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
const remarkPlugins = [remarkGfm];
const rehypePlugins = [rehypeHighlight];
export function Markdown({children}: {children: string}) {
  return <div className="markdown"><ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins} components={{a:({children,...props}) => <a {...props} target="_blank" rel="noreferrer">{children}</a>}}>{children}</ReactMarkdown></div>;
}
export function Python({code}: {code: string}) {
  // Longer fences safely contain arbitrary backticks in the source.
  const fence = '`'.repeat(Math.max(3,...(code.match(/`+/g) || []).map(s => s.length+1)));
  return <div className="code-block"><div className="code-label">Python</div><Markdown>{`${fence}python\n${code}\n${fence}`}</Markdown></div>;
}
