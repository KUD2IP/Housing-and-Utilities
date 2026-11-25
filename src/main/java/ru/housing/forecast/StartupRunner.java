package ru.housing.forecast;

import java.nio.file.Path;
import java.util.List;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import ru.housing.forecast.ingest.IngestionService;
import ru.housing.forecast.ml.PipelineService;

@Component
public class StartupRunner implements ApplicationRunner {
    private final IngestionService ingestion;
    private final PipelineService pipeline;
    private final AppProperties properties;
    private final AppState state;
    private final JdbcTemplate jdbc;

    public StartupRunner(
            IngestionService ingestion,
            PipelineService pipeline,
            AppProperties properties,
            AppState state,
            JdbcTemplate jdbc) {
        this.ingestion = ingestion;
        this.pipeline = pipeline;
        this.properties = properties;
        this.state = state;
        this.jdbc = jdbc;
    }

    @Override
    public void run(ApplicationArguments args) {
        try {
            state.setStage("применение миграций");
            DatabaseMigrations.apply(jdbc.getDataSource());
            state.setStage("загрузка исходной партии");
            ingestion.ingest(Path.of(properties.getSources().getInitial()), "initial", true);
            if (properties.getWeather().isEnabled()) {
                state.setStage("запрос внешней погоды");
                var weather = properties.getWeather();
                ingestion.ingestExternal(
                        weather.getUrl(),
                        weather.getLatitude(),
                        weather.getLongitude(),
                        weather.getTimeoutSeconds(),
                        weather.getRecentDays());
            }
            Integer models = jdbc.queryForObject(
                    "SELECT COUNT(*) FROM model_versions WHERE status = 'production'", Integer.class);
            if (models == null || models == 0) {
                state.setStage("обучение моделей и проверка качества");
                pipeline.run(List.of("train"));
            } else {
                state.setStage("прогноз на уже загруженных данных");
                pipeline.run(List.of("score"));
            }
            state.setError(null);
        } catch (Exception exception) {
            state.setError(exception.getMessage());
        } finally {
            state.setStage("готово");
            state.setReady(true);
            System.out.println("Контур: http://localhost:8080");
        }
    }
}
