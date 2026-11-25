export function Boot({ stage }) {
  return (
    <div className="boot">
      <div className="boot-card">
        <p className="eyebrow">Жилищно-коммунальное хозяйство</p>
        <h1>Контур готовит прогноз</h1>
        <p>{stage}</p>
        <div className="pulse" style={{ marginTop: 22 }} />
      </div>
    </div>
  );
}
