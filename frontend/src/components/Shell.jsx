export function Shell({ view, setView, views, children }) {
  return (
    <div className="app">
      <aside>
        <div className="brand">
          <span className="mark" aria-hidden="true" />
          <div>
            <strong>Контур</strong>
            <span>тепло группы домов</span>
          </div>
        </div>
        <nav>
          {views.map(([id, title], index) => (
            <button key={id} type="button" className={view === id ? "active" : ""} onClick={() => setView(id)}>
              {title}
              <small>0{index + 1}</small>
            </button>
          ))}
        </nav>
        <p className="aside-note">Панель диспетчера управляющей компании. Каждый экран отвечает на один рабочий вопрос.</p>
      </aside>
      <main>{children}</main>
    </div>
  );
}
