package ru.housing.forecast.ingest;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Duration;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;
import javax.sql.DataSource;
import org.springframework.stereotype.Service;

@Service
public class IngestionService {
    private static final Pattern SENSOR = Pattern.compile(
            "^(\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}) house=([A-Za-z0-9]+) sensor=([a-z_]+) value=([-+]?\\d+(?:\\.\\d+)?) status=(ok|warn|alarm)(?: msg=(.*))?$");
    private static final Pattern INCIDENT = Pattern.compile("^(\\d{4}-\\d{2}-\\d{2})\t([A-Za-z0-9]+)\t([^\t]+)\t(.+)$");
    private static final Pattern DAY = Pattern.compile("\\d{4}-\\d{2}-\\d{2}");

    private final DataSource dataSource;
    private final ObjectMapper mapper = new ObjectMapper();

    public IngestionService(DataSource dataSource) {
        this.dataSource = dataSource;
    }

    public List<LoadReport> ingest(Path root, String mode, boolean requireBuildings) {
        List<LoadReport> reports = new ArrayList<>();
        if (root == null || !Files.isDirectory(root)) {
            reports.add(finish(mode, "sources", "FAILED", 0, 0, 0, 0,
                    "Каталог партии недоступен: " + root));
            return reports;
        }
        reports.add(loadBuildings(root.resolve("buildings.json"), mode, requireBuildings));
        reports.add(loadWeather(root, mode));
        reports.add(loadIncidents(root.resolve("incidents"), mode));
        reports.add(loadSensors(root.resolve("sensors"), mode));
        return reports;
    }

    public LoadReport ingestExternal(String url, double latitude, double longitude, int timeoutSeconds, int recentDays) {
        String source = "open-meteo";
        LocalDate end = LocalDate.now().minusDays(1);
        LocalDate start = end.minusDays(Math.max(recentDays - 1, 0));
        String requestUrl = url
                + "?latitude=" + latitude
                + "&longitude=" + longitude
                + "&start_date=" + start
                + "&end_date=" + end
                + "&daily=temperature_2m_mean,temperature_2m_min,temperature_2m_max,precipitation_sum,wind_speed_10m_max,relative_humidity_2m_mean"
                + "&timezone=Europe%2FMoscow";
        try {
            HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(timeoutSeconds)).build();
            HttpRequest request = HttpRequest.newBuilder(URI.create(requestUrl))
                    .timeout(Duration.ofSeconds(timeoutSeconds))
                    .GET()
                    .build();
            HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            if (response.statusCode() != 200) {
                return finish("external", source, "FAILED", 0, 0, 0, 0,
                        "Внешний архив ответил кодом " + response.statusCode() + ". Обучение идёт по локальному архиву.");
            }
            JsonNode daily = mapper.readTree(response.body()).path("daily");
            if (!daily.has("time")) {
                return finish("external", source, "FAILED", 0, 0, 0, 0,
                        "В ответе Open-Meteo нет суточного блока. Локальный архив не затронут.");
            }
            int read = 0;
            int inserted = 0;
            int skipped = 0;
            try (Connection connection = dataSource.getConnection()) {
                connection.setAutoCommit(false);
                String sql = """
                        INSERT INTO weather_external (date, t_avg, t_min, t_max, precipitation_mm, wind_speed, humidity, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(date) DO NOTHING
                        """;
                try (PreparedStatement statement = connection.prepareStatement(sql)) {
                    JsonNode time = daily.path("time");
                    String fetchedAt = LocalDateTime.now().withNano(0).toString();
                    for (int i = 0; i < time.size(); i++) {
                        read++;
                        JsonNode temp = daily.path("temperature_2m_mean").path(i);
                        if (temp.isNull() || temp.isMissingNode()) {
                            skipped++;
                            continue;
                        }
                        statement.setString(1, time.get(i).asText());
                        setNullable(statement, 2, temp);
                        setNullable(statement, 3, daily.path("temperature_2m_min").path(i));
                        setNullable(statement, 4, daily.path("temperature_2m_max").path(i));
                        setNullable(statement, 5, daily.path("precipitation_sum").path(i));
                        setNullable(statement, 6, daily.path("wind_speed_10m_max").path(i));
                        setNullable(statement, 7, daily.path("relative_humidity_2m_mean").path(i));
                        statement.setString(8, fetchedAt);
                        int updated = statement.executeUpdate();
                        if (updated > 0) {
                            inserted++;
                        } else {
                            skipped++;
                        }
                    }
                }
                connection.commit();
            }
            return finish("external", source, "SUCCESS", read, inserted, skipped, 0,
                    inserted == 0
                            ? "Повторный запрос не добавил дат: внешний архив уже загружен."
                            : "Догружены новые даты внешнего архива. В обучение они не подмешиваются.");
        } catch (Exception exception) {
            return finish("external", source, "FAILED", 0, 0, 0, 0,
                    "Источник недоступен (" + exception.getClass().getSimpleName() + "). Система продолжает работу на локальном архиве.");
        }
    }

    private LoadReport loadBuildings(Path file, String mode, boolean required) {
        if (!Files.isRegularFile(file)) {
            return finish(mode, "buildings", required ? "FAILED" : "SKIPPED", 0, 0, 0, 0,
                    required ? "Нет паспортов домов." : "В этой партии паспортов нет.");
        }
        int read = 0;
        int inserted = 0;
        String outcome = "loaded";
        try (Connection connection = dataSource.getConnection()) {
            connection.setAutoCommit(false);
            if (alreadyLoaded(connection, file)) {
                connection.rollback();
                outcome = "cached";
            } else {
                JsonNode root = mapper.readTree(Files.readString(file));
                if (!root.isArray()) {
                    connection.rollback();
                    outcome = "invalid";
                } else {
                    String sql = """
                            INSERT INTO buildings (
                                building_id, address, district, year_built, floors, total_area_m2,
                                apartments, residents, wall_type, heat_system, lat, lon
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(building_id) DO UPDATE SET
                                address=excluded.address, district=excluded.district, year_built=excluded.year_built,
                                floors=excluded.floors, total_area_m2=excluded.total_area_m2, apartments=excluded.apartments,
                                residents=excluded.residents, wall_type=excluded.wall_type, heat_system=excluded.heat_system,
                                lat=excluded.lat, lon=excluded.lon
                            """;
                    try (PreparedStatement statement = connection.prepareStatement(sql)) {
                        for (JsonNode node : root) {
                            read++;
                            if (!node.hasNonNull("building_id") || !node.hasNonNull("address")) {
                                continue;
                            }
                            statement.setString(1, node.path("building_id").asText());
                            statement.setString(2, node.path("address").asText());
                            statement.setString(3, node.path("district").asText());
                            statement.setInt(4, node.path("year_built").asInt());
                            statement.setInt(5, node.path("floors").asInt());
                            statement.setDouble(6, node.path("total_area_m2").asDouble());
                            statement.setInt(7, node.path("apartments").asInt());
                            statement.setInt(8, node.path("residents").asInt());
                            statement.setString(9, node.path("wall_type").asText());
                            statement.setString(10, node.path("heat_system").asText());
                            statement.setDouble(11, node.path("lat").asDouble());
                            statement.setDouble(12, node.path("lon").asDouble());
                            statement.executeUpdate();
                            inserted++;
                        }
                    }
                    markLoaded(connection, file);
                    connection.commit();
                }
            }
        } catch (Exception exception) {
            return finish(mode, "buildings", "FAILED", 0, 0, 0, 0, "Паспорта не прочитаны: " + exception.getMessage());
        }
        if ("cached".equals(outcome)) {
            return finish(mode, "buildings", "SUCCESS", 0, 0, 1, 0, "Файл паспортов уже загружен.");
        }
        if ("invalid".equals(outcome)) {
            return finish(mode, "buildings", "FAILED", 0, 0, 0, 1, "Паспорта должны быть массивом JSON.");
        }
        return finish(mode, "buildings", "SUCCESS", read, inserted, 0, read - inserted, "Паспорта домов записаны.");
    }

    private LoadReport loadWeather(Path root, String mode) {
        List<Path> files = list(root, ".csv");
        if (files.isEmpty()) {
            return finish(mode, "weather", "SKIPPED", 0, 0, 0, 0, "В партии нет файла погоды.");
        }
        return loadFiles(mode, "weather", files, (connection, file, counter) -> readWeather(connection, file, counter));
    }

    private LoadReport loadIncidents(Path folder, String mode) {
        if (!Files.isDirectory(folder)) {
            return finish(mode, "incidents", "SKIPPED", 0, 0, 0, 0, "В партии нет диспетчерского журнала.");
        }
        return loadFiles(mode, "incidents", list(folder, ".txt"), (connection, file, counter) -> readIncidents(connection, file, counter));
    }

    private LoadReport loadSensors(Path folder, String mode) {
        if (!Files.isDirectory(folder)) {
            return finish(mode, "sensor_logs", "FAILED", 0, 0, 0, 0, "Нет каталога журналов приборов учёта.");
        }
        return loadFiles(mode, "sensor_logs", list(folder, ".log"), (connection, file, counter) -> readSensors(connection, file, counter));
    }

    private LoadReport loadFiles(String mode, String source, List<Path> files, FileLoader loader) {
        int read = 0;
        int inserted = 0;
        int skipped = 0;
        int rejected = 0;
        int filesSkipped = 0;
        try (Connection connection = dataSource.getConnection()) {
            connection.setAutoCommit(false);
            for (Path file : files) {
                if (alreadyLoaded(connection, file)) {
                    filesSkipped++;
                    continue;
                }
                Counter counter = new Counter();
                loader.load(connection, file, counter);
                read += counter.read;
                inserted += counter.inserted;
                skipped += counter.skipped;
                rejected += counter.rejected;
                markLoaded(connection, file);
            }
            connection.commit();
        } catch (Exception exception) {
            return finish(mode, source, "FAILED", read, inserted, skipped, rejected,
                    "Загрузка прервана: " + exception.getMessage());
        }
        String status = rejected > 0 ? "PARTIAL" : "SUCCESS";
        String message = filesSkipped == files.size()
                ? "Повторный запуск: новых файлов нет, хранилище не изменилось."
                : "Новые файлы догружены. Повтор тех же строк не создаёт дубликаты.";
        return finish(mode, source, status, read, inserted, skipped + filesSkipped, rejected, message);
    }

    private void readWeather(Connection connection, Path file, Counter counter) throws IOException, SQLException {
        String sql = """
                INSERT INTO weather_daily (date, t_avg, t_min, t_max, precipitation_mm, wind_speed, humidity, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'archive')
                ON CONFLICT(date) DO NOTHING
                """;
        try (PreparedStatement statement = connection.prepareStatement(sql)) {
            for (String line : Files.readAllLines(file, StandardCharsets.UTF_8)) {
                if (line.isBlank() || line.startsWith("date,")) {
                    continue;
                }
                counter.read++;
                String[] parts = line.split(",", -1);
                if (parts.length < 7 || !DAY.matcher(parts[0].trim()).matches()) {
                    counter.rejected++;
                    continue;
                }
                try {
                    statement.setString(1, parts[0].trim());
                    statement.setDouble(2, Double.parseDouble(parts[1].trim()));
                    statement.setDouble(3, Double.parseDouble(parts[2].trim()));
                    statement.setDouble(4, Double.parseDouble(parts[3].trim()));
                    statement.setDouble(5, Double.parseDouble(parts[4].trim()));
                    statement.setDouble(6, Double.parseDouble(parts[5].trim()));
                    statement.setDouble(7, Double.parseDouble(parts[6].trim()));
                } catch (NumberFormatException exception) {
                    counter.rejected++;
                    continue;
                }
                if (statement.executeUpdate() > 0) {
                    counter.inserted++;
                } else {
                    counter.skipped++;
                }
            }
        }
    }

    private void readSensors(Connection connection, Path file, Counter counter) throws IOException, SQLException {
        Set<String> buildings = knownBuildings(connection);
        String sql = """
                INSERT INTO sensor_readings (ts, building_id, sensor_type, value, status, message, source_file)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ts, building_id, sensor_type) DO NOTHING
                """;
        try (PreparedStatement statement = connection.prepareStatement(sql)) {
            for (String line : Files.readAllLines(file, StandardCharsets.UTF_8)) {
                if (line.isBlank()) {
                    continue;
                }
                counter.read++;
                Matcher matcher = SENSOR.matcher(line.trim());
                if (!matcher.matches()) {
                    counter.rejected++;
                    continue;
                }
                String buildingId = matcher.group(2);
                String type = matcher.group(3);
                double value = Double.parseDouble(matcher.group(4));
                if (!buildings.contains(buildingId) || !sensorValueOk(type, value)) {
                    counter.rejected++;
                    continue;
                }
                try {
                    LocalDateTime.parse(matcher.group(1));
                } catch (DateTimeParseException exception) {
                    counter.rejected++;
                    continue;
                }
                statement.setString(1, matcher.group(1));
                statement.setString(2, buildingId);
                statement.setString(3, type);
                statement.setDouble(4, value);
                statement.setString(5, matcher.group(5));
                statement.setString(6, matcher.group(6));
                statement.setString(7, file.getFileName().toString());
                if (statement.executeUpdate() > 0) {
                    counter.inserted++;
                } else {
                    counter.skipped++;
                }
            }
        }
    }

    private void readIncidents(Connection connection, Path file, Counter counter) throws IOException, SQLException {
        Set<String> buildings = knownBuildings(connection);
        String sql = """
                INSERT INTO incidents (incident_key, date, building_id, severity, text, source_file)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(incident_key) DO NOTHING
                """;
        try (PreparedStatement statement = connection.prepareStatement(sql)) {
            for (String line : Files.readAllLines(file, StandardCharsets.UTF_8)) {
                if (line.isBlank()) {
                    continue;
                }
                counter.read++;
                Matcher matcher = INCIDENT.matcher(line.trim());
                if (!matcher.matches() || !buildings.contains(matcher.group(2))) {
                    counter.rejected++;
                    continue;
                }
                String key = sha256(matcher.group(1) + "|" + matcher.group(2) + "|" + matcher.group(4));
                statement.setString(1, key);
                statement.setString(2, matcher.group(1));
                statement.setString(3, matcher.group(2));
                statement.setString(4, matcher.group(3));
                statement.setString(5, matcher.group(4));
                statement.setString(6, file.getFileName().toString());
                if (statement.executeUpdate() > 0) {
                    counter.inserted++;
                } else {
                    counter.skipped++;
                }
            }
        }
    }

    private static boolean sensorValueOk(String type, double value) {
        return switch (type) {
            case "heat_kwh" -> value >= 0 && value <= 50_000;
            case "supply_c", "return_c" -> value >= 0 && value <= 150;
            case "alarm" -> value >= 0 && value <= 1;
            default -> false;
        };
    }

    private Set<String> knownBuildings(Connection connection) throws SQLException {
        Set<String> ids = new HashSet<>();
        try (ResultSet rows = connection.createStatement().executeQuery("SELECT building_id FROM buildings")) {
            while (rows.next()) {
                ids.add(rows.getString(1));
            }
        }
        return ids;
    }

    private boolean alreadyLoaded(Connection connection, Path file) throws IOException, SQLException {
        String path = file.toAbsolutePath().normalize().toString();
        String checksum = checksum(file);
        try (PreparedStatement statement = connection.prepareStatement("SELECT checksum FROM ingest_files WHERE path = ?")) {
            statement.setString(1, path);
            try (ResultSet rows = statement.executeQuery()) {
                return rows.next() && checksum.equals(rows.getString(1));
            }
        }
    }

    private void markLoaded(Connection connection, Path file) throws IOException, SQLException {
        try (PreparedStatement statement = connection.prepareStatement(
                "INSERT INTO ingest_files (path, checksum, processed_at) VALUES (?, ?, ?) "
                        + "ON CONFLICT(path) DO UPDATE SET checksum=excluded.checksum, processed_at=excluded.processed_at")) {
            statement.setString(1, file.toAbsolutePath().normalize().toString());
            statement.setString(2, checksum(file));
            statement.setString(3, LocalDateTime.now().withNano(0).toString());
            statement.executeUpdate();
        }
    }

    private LoadReport finish(String mode, String source, String status, int read, int inserted, int skipped, int rejected, String message) {
        String started = LocalDateTime.now().withNano(0).toString();
        try (Connection connection = dataSource.getConnection();
             PreparedStatement statement = connection.prepareStatement(
                     """
                     INSERT INTO load_journal (
                         started_at, finished_at, source, mode, status, rows_read, rows_inserted, rows_skipped, rows_rejected, message
                     ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                     """)) {
            statement.setString(1, started);
            statement.setString(2, LocalDateTime.now().withNano(0).toString());
            statement.setString(3, source);
            statement.setString(4, mode);
            statement.setString(5, status);
            statement.setInt(6, read);
            statement.setInt(7, inserted);
            statement.setInt(8, skipped);
            statement.setInt(9, rejected);
            statement.setString(10, message);
            statement.executeUpdate();
        } catch (SQLException exception) {
            message = message + " Журнал не записан: " + exception.getMessage();
        }
        return new LoadReport(source, status, read, inserted, skipped, rejected, message);
    }

    private static void setNullable(PreparedStatement statement, int index, JsonNode node) throws SQLException {
        if (node == null || node.isNull() || node.isMissingNode()) {
            statement.setNull(index, java.sql.Types.REAL);
        } else {
            statement.setDouble(index, node.asDouble());
        }
    }

    private static List<Path> list(Path folder, String suffix) {
        if (!Files.exists(folder)) {
            return List.of();
        }
        try (Stream<Path> walk = Files.walk(folder)) {
            return walk.filter(path -> Files.isRegularFile(path) && path.getFileName().toString().endsWith(suffix))
                    .sorted(Comparator.comparing(Path::toString))
                    .toList();
        } catch (IOException exception) {
            return List.of();
        }
    }

    private static String checksum(Path file) throws IOException {
        try {
            MessageDigest digest = MessageDigest.getInstance("MD5");
            digest.update(Files.readAllBytes(file));
            return HexFormat.of().formatHex(digest.digest());
        } catch (Exception exception) {
            throw new IOException(exception);
        }
    }

    private static String sha256(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception exception) {
            throw new IllegalStateException(exception);
        }
    }

    private interface FileLoader {
        void load(Connection connection, Path file, Counter counter) throws IOException, SQLException;
    }

    private static final class Counter {
        private int read;
        private int inserted;
        private int skipped;
        private int rejected;
    }
}
