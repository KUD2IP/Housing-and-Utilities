package ru.housing.forecast;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.LocalDateTime;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HashSet;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import javax.sql.DataSource;
import org.springframework.core.io.Resource;
import org.springframework.core.io.support.PathMatchingResourcePatternResolver;

/**
 * Версионные миграции: один файл на таблицу в db/migration.
 * Flyway не используется: у актуальной ветки нет модуля SQLite.
 */
public final class DatabaseMigrations {
    private static final Pattern VERSION = Pattern.compile("V(\\d+)");

    private DatabaseMigrations() {
    }

    public static void apply(DataSource dataSource) throws IOException {
        Resource[] resources = new PathMatchingResourcePatternResolver().getResources("classpath*:db/migration/V*.sql");
        Resource[] ordered = Arrays.stream(resources)
                .sorted(Comparator.comparingInt(DatabaseMigrations::versionOf))
                .toArray(Resource[]::new);
        try (Connection connection = dataSource.getConnection()) {
            connection.setAutoCommit(false);
            try (Statement statement = connection.createStatement()) {
                statement.execute("""
                        CREATE TABLE IF NOT EXISTS schema_migrations (
                            version TEXT PRIMARY KEY,
                            description TEXT NOT NULL,
                            applied_at TEXT NOT NULL
                        )
                        """);
            }
            Set<String> applied = appliedVersions(connection);
            for (Resource resource : ordered) {
                String filename = resource.getFilename();
                String version = "V" + versionOf(resource);
                if (filename == null || applied.contains(version)) {
                    continue;
                }
                String sql = new String(resource.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
                try (Statement statement = connection.createStatement()) {
                    for (String part : sql.split(";")) {
                        String trimmed = part.trim();
                        if (!trimmed.isEmpty()) {
                            statement.execute(trimmed);
                        }
                    }
                }
                try (PreparedStatement insert = connection.prepareStatement(
                        "INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)")) {
                    insert.setString(1, version);
                    insert.setString(2, filename);
                    insert.setString(3, LocalDateTime.now().withNano(0).toString());
                    insert.executeUpdate();
                }
            }
            connection.commit();
        } catch (SQLException exception) {
            throw new IOException("Миграции не применились", exception);
        }
    }

    private static Set<String> appliedVersions(Connection connection) throws SQLException {
        Set<String> applied = new HashSet<>();
        try (Statement statement = connection.createStatement();
             ResultSet rows = statement.executeQuery("SELECT version FROM schema_migrations")) {
            while (rows.next()) {
                applied.add(rows.getString(1));
            }
        }
        return applied;
    }

    private static int versionOf(Resource resource) {
        String filename = resource.getFilename() == null ? "" : resource.getFilename();
        Matcher matcher = VERSION.matcher(filename);
        if (!matcher.find()) {
            throw new IllegalStateException("Имя миграции без номера: " + filename);
        }
        return Integer.parseInt(matcher.group(1));
    }
}
