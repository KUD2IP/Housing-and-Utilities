export function Toast({ text }) {
  if (!text) return null;
  return <div className="toast">{text}</div>;
}

export function PageHead({ title, lede, children }) {
  return (
    <div className="view-head">
      <div>
        <p className="eyebrow">Контур</p>
        <h1>{title}</h1>
        {lede ? <p className="lede">{lede}</p> : null}
      </div>
      {children}
    </div>
  );
}
