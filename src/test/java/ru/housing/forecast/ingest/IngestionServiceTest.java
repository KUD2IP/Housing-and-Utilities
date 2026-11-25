package ru.housing.forecast.ingest;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import javax.sql.DataSource;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.sqlite.SQLiteDataSource;
import ru.housing.forecast.DatabaseMigrations;

class IngestionServiceTest {
    @TempDir
    Path temp;

    @Test
    void secondRunInsertsNothingAndBadLinesAreRejected() throws Exception {
        Path root = temp.resolve("initial");
        Files.createDirectories(root.resolve("sensors"));
        Files.createDirectories(root.resolve("incidents"));
        Files.writeString(root.resolve("buildings.json"), """
                [{"building_id":"H01","address":"ул. Лесная, д. 4","district":"ЦАО","year_built":1972,
                  "floors":9,"total_area_m2":6240,"apartments":108,"residents":248,
                  "wall_type":"panel","heat_system":"central","lat":55.75,"lon":37.62}]
                """);
        Files.writeString(root.resolve("weather.csv"), """
                date,t_avg,t_min,t_max,precipitation_mm,wind_speed,humidity
                2024-01-15,-8.0,-12.0,-4.0,0.2,3.0,80
                not-a-date,abc,1,2,3,4,5
                2024-01-15,-8.0,-12.0,-4.0,0.2,3.0,80
                """);
        Files.writeString(root.resolve("sensors/day.log"), """
                2024-01-15T06:00:00 house=H01 sensor=heat_kwh value=1800.00 status=ok
                мусорная строка
                2024-01-15T06:00:00 house=H99 sensor=heat_kwh value=10 status=ok
                2024-01-15T06:05:00 house=H01 sensor=heat_kwh value=-5 status=ok
                """);
        Files.writeString(root.resolve("incidents/journal.txt"),
                "2024-01-15\tH01\tавария\tПрорыв, отключение отопления на 6 часов.\n");

        DataSource dataSource = database();
        IngestionService service = new IngestionService(dataSource);
        List<LoadReport> first = service.ingest(root, "initial", true);
        assertTrue(first.stream().anyMatch(report -> report.rowsRejected() > 0));
        int sensors = count(dataSource, "sensor_readings");
        int weather = count(dataSource, "weather_daily");
        assertEquals(1, sensors);
        assertEquals(1, weather);

        List<LoadReport> second = service.ingest(root, "initial", true);
        assertTrue(second.stream().allMatch(report -> report.rowsInserted() == 0));
        assertEquals(sensors, count(dataSource, "sensor_readings"));
        assertEquals(weather, count(dataSource, "weather_daily"));

        try (var connection = dataSource.getConnection(); var statement = connection.createStatement()) {
            statement.execute("DELETE FROM ingest_files");
        }
        service.ingest(root, "initial", true);
        assertEquals(sensors, count(dataSource, "sensor_readings"));
        assertEquals(weather, count(dataSource, "weather_daily"));
        assertEquals(1, count(dataSource, "incidents"));
    }

    @Test
    void missingFolderDoesNotThrow() throws Exception {
        DataSource dataSource = database();
        IngestionService service = new IngestionService(dataSource);
        List<LoadReport> reports = service.ingest(temp.resolve("absent"), "delta", false);
        assertEquals("FAILED", reports.get(0).status());
        assertEquals(1, count(dataSource, "load_journal"));
    }

    private DataSource database() throws Exception {
        SQLiteDataSource dataSource = new SQLiteDataSource();
        dataSource.setUrl("jdbc:sqlite:" + temp.resolve("test.db"));
        DatabaseMigrations.apply(dataSource);
        DatabaseMigrations.apply(dataSource);
        return dataSource;
    }

    private static int count(DataSource dataSource, String table) throws Exception {
        try (var connection = dataSource.getConnection();
             var statement = connection.createStatement();
             var rows = statement.executeQuery("SELECT COUNT(*) FROM " + table)) {
            rows.next();
            return rows.getInt(1);
        }
    }
}
