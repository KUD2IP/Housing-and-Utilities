package ru.housing.forecast.ml;

import java.time.LocalDateTime;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ModelAdmin {
    private final JdbcTemplate jdbc;

    public ModelAdmin(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Transactional
    public void promote(String version) {
        String status = statusOf(version);
        if (!"candidate".equals(status)) {
            throw new IllegalArgumentException("Вручную выводится только версия со статусом «кандидат».");
        }
        jdbc.update("UPDATE model_versions SET status = 'archived' WHERE status = 'production'");
        jdbc.update("UPDATE model_versions SET status = 'production' WHERE version = ?", version);
        event(version, "manual_promote", "Диспетчер вывел кандидата в эксплуатацию вручную.");
    }

    @Transactional
    public void rollback(String version) {
        String status = statusOf(version);
        if (!"archived".equals(status)) {
            throw new IllegalArgumentException("Вернуть можно только архивную версию.");
        }
        jdbc.update("UPDATE model_versions SET status = 'archived' WHERE status = 'production'");
        jdbc.update("UPDATE model_versions SET status = 'production' WHERE version = ?", version);
        event(version, "rollback", "Рабочая версия заменена предыдущей архивной.");
    }

    private String statusOf(String version) {
        var rows = jdbc.queryForList("SELECT status FROM model_versions WHERE version = ?", version);
        if (rows.isEmpty()) {
            throw new IllegalArgumentException("Версия не найдена.");
        }
        return String.valueOf(rows.get(0).get("status"));
    }

    private void event(String version, String action, String message) {
        jdbc.update(
                "INSERT INTO model_events (created_at, version, action, message) VALUES (?, ?, ?, ?)",
                LocalDateTime.now().withNano(0).toString(),
                version,
                action,
                message);
    }
}
