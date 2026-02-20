import ReactMarkdown from "react-markdown";

const ERROS_TECNICOS = [
  'INTEGRITY_WARNING',
  'EXCERPT_MISMATCH',
  'RANGE_INVALID',
  'PAGE_MISMATCH',
  'ITEM_NOT_FOUND',
  'MISSING_CITATION',
  'MISSING_RATIONALE',
  'SEM_PROVA_DETERMINANTE',
  'match_ratio=',
];

function filterTechnicalErrors(content: string): string {
  return content
    .split('\n')
    .filter(line => !ERROS_TECNICOS.some(keyword => line.includes(keyword)))
    .join('\n');
}

interface MarkdownContentProps {
  content: string;
}

const MarkdownContent = ({ content }: MarkdownContentProps) => {
  const filtered = filterTechnicalErrors(content);

  return (
    <div className="prose prose-sm dark:prose-invert max-w-none text-muted-foreground leading-relaxed">
      <ReactMarkdown
        components={{
          h1: ({ children }) => <h1 className="text-lg font-semibold text-foreground mt-4 mb-2">{children}</h1>,
          h2: ({ children }) => <h2 className="text-base font-semibold text-foreground mt-3 mb-2">{children}</h2>,
          h3: ({ children }) => <h3 className="text-sm font-semibold text-foreground mt-2 mb-1">{children}</h3>,
          p: ({ children }) => <p className="mb-2 text-muted-foreground">{children}</p>,
          ul: ({ children }) => <ul className="list-disc pl-5 mb-2 space-y-1">{children}</ul>,
          ol: ({ children }) => <ol className="list-decimal pl-5 mb-2 space-y-1">{children}</ol>,
          li: ({ children }) => <li className="text-muted-foreground">{children}</li>,
          strong: ({ children }) => <strong className="text-foreground font-semibold">{children}</strong>,
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-accent pl-4 my-2 italic text-muted-foreground">
              {children}
            </blockquote>
          ),
          code: ({ children }) => (
            <code className="bg-muted px-1.5 py-0.5 rounded text-xs font-mono">{children}</code>
          ),
        }}
      >
        {filtered}
      </ReactMarkdown>
    </div>
  );
};

export default MarkdownContent;
