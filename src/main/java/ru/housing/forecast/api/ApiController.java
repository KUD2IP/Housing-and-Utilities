package ru.housing.forecast.api;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.yaml.snakeyaml.Yaml;
import ru.housing.forecast.AppProperties;
import ru.housing.forecast.AppState;
import ru.housing.forecast.ingest.IngestionService;
import ru.housing.forecast.ml.ModelAdmin;
import ru.housing.forecast.ml.PipelineService;

@RestController
public class ApiController {
    private static final Pattern BUILDING = Pattern.compile("H\\d{2}");
    private static final Pattern DAY = Pattern.compile("\\d{4}-\\d{2}-\\d{2}");
    private static final Pattern VERSION = Pattern.compile("v\\d{14}-[a-z_]+");
    private static final Pattern EXPERIMENT = Pattern.compile("[A-Za-z0-9\\-]{4,80}");

    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper;
    private final AppState state;
    private final AppProperties properties;
    private final IngestionService ingestion;
    private final PipelineService pipeline;
    private final ModelAdmin admin;
    private final double tariff;
    private final int horizon;

    public ApiController(
            JdbcTemplate jdbc,
            ObjectMapper mapper,
            AppState state,
            AppProperties properties,
            IngestionService ingestion,
            PipelineService pipeline,
            ModelAdmin admin) throws java.io.IOException {
        this.jdbc = jdbc;
        this.mapper = mapper;
        this.state = state;
        this.properties = properties;
        this.ingestion = ingestion;
        this.pipeline = pipeline;
        this.admin = admin;
        Map<String, Object> config = new Yaml().load(Files.readString(Path.of("config/pipeline.yaml")));
        this.tariff = ((Number) config.get("tariff_rub_per_gcal")).doubleValue();
        this.horizon = ((Number) config.get("forecast_horizon")).intValue();
    }

    @GetMapping("/api/health")
    public Map<String, Object> health() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("ready", state.isReady());
        body.put("stage", state.getStage());
        body.put("error", state.getError());
        return body;
    }

    @GetMapping("/api/bootstrap")
    public Map<String, Object> bootstrap() {
        Map<String, Object> production = production();
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("tariff", tariff);
        body.put("horizon", horizon);
        body.put("error", state.getError());
        body.put("production", production);
        body.put("metrics", production == null ? List.of() : jdbc.queryForList(
                """
                SELECT algorithm, split, mae, mape, within_10 AS within10, mae_rub AS maeRub, n_obs AS nObs
                FROM metrics WHERE version = ?
                """,
                production.get("version")));
        body.put("lastActualDate", queryString("SELECT MAX(date) FROM daily_actuals WHERE heat_gcal IS NOT NULL"));
        body.put("buildings", buildings(production));
        body.put("monitoring", latestMonitoring());
        return body;
    }

    @GetMapping("/api/series")
    public Map<String, Object> series(@RequestParam String buildingId) {
        requireBuilding(buildingId);
        Map<String, Object> production = production();
        String version = production == null ? "" : String.valueOf(production.get("version"));
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("buildingId", buildingId);
        body.put("actuals", jdbc.queryForList(
                "SELECT date, heat_gcal AS heat FROM daily_actuals WHERE building_id = ? ORDER BY date",
                buildingId));
        body.put("predictions", version.isBlank() ? List.of() : jdbc.queryForList(
                """
                SELECT date, algorithm, y_pred AS yPred, split
                FROM predictions WHERE version = ? AND building_id = ? ORDER BY date
                """,
                version, buildingId));
        body.put("forecast", version.isBlank() ? List.of() : jdbc.queryForList(
                """
                SELECT date, y_pred AS yPred, y_lower AS yLower, y_upper AS yUpper
                FROM forecasts WHERE version = ? AND building_id = ? ORDER BY date
                """,
                version, buildingId));
        return body;
    }

    @GetMapping("/api/models")
    public Map<String, Object> models() {
        Map<String, Object> production = production();
        String version = production == null ? "" : String.valueOf(production.get("version"));
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("production", production);
        body.put("metrics", version.isBlank() ? List.of() : jdbc.queryForList(
                """
                SELECT algorithm, split, mae, mape, within_10 AS within10, mae_rub AS maeRub, n_obs AS nObs
                FROM metrics WHERE version = ? ORDER BY algorithm, split
                """,
                version));
        body.put("versions", jdbc.queryForList(
                """
                SELECT version, created_at AS createdAt, algorithm, status, train_start AS trainStart,
                       train_end AS trainEnd, valid_start AS validStart, valid_end AS validEnd,
                       holdout_start AS holdoutStart, holdout_end AS holdoutEnd, metrics, notes
                FROM model_versions ORDER BY created_at DESC
                """));
        body.put("events", jdbc.queryForList(
                """
                SELECT created_at AS createdAt, version, action, message
                FROM model_events ORDER BY id DESC LIMIT 12
                """));
        return body;
    }

    @GetMapping("/api/experiments")
    public List<Map<String, Object>> experiments() {
        return jdbc.queryForList(
                """
                SELECT experiment_id AS experimentId, created_at AS createdAt, algorithm, params,
                       data_version AS dataVersion, seed, search_budget AS searchBudget,
                       val_mae AS valMae, val_mape AS valMape, val_within_10 AS valWithin10,
                       selected, notes
                FROM experiments ORDER BY created_at DESC, experiment_id
                """);
    }

    @GetMapping("/api/monitoring")
    public Map<String, Object> monitoring() {
        return Map.of("snapshots", monitoringSnapshots());
    }

    @GetMapping("/api/loads")
    public Map<String, Object> loads() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("journal", jdbc.queryForList(
                """
                SELECT started_at AS startedAt, source, mode, status, rows_read AS rowsRead,
                       rows_inserted AS rowsInserted, rows_skipped AS rowsSkipped,
                       rows_rejected AS rowsRejected, message
                FROM load_journal ORDER BY id DESC LIMIT 40
                """));
        body.put("quality", jdbc.queryForList(
                "SELECT check_name AS checkName, status, value, details FROM data_quality ORDER BY id"));
        body.put("externalDays", queryInt("SELECT COUNT(*) FROM weather_external"));
        return body;
    }

    @GetMapping("/api/importance")
    public Map<String, Object> importance() {
        Map<String, Object> production = production();
        if (production == null) {
            return Map.of("rows", List.of());
        }
        return Map.of("version", production.get("version"), "algorithm", production.get("algorithm"), "rows", jdbc.queryForList(
                """
                SELECT method, period, feature, importance
                FROM feature_importance WHERE version = ?
                """,
                production.get("version")));
    }

    @GetMapping("/api/dependence")
    public Map<String, Object> dependence() throws java.io.IOException {
        Map<String, Object> production = production();
        if (production == null) {
            return Map.of();
        }
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT feature, grid_json AS gridJson FROM partial_dependence WHERE version = ?",
                production.get("version"));
        if (rows.isEmpty()) {
            return Map.of();
        }
        Map<String, Object> grid = mapper.readValue(String.valueOf(rows.get(0).get("gridJson")), new TypeReference<>() {});
        grid.put("version", production.get("version"));
        return grid;
    }

    @GetMapping("/api/explain")
    public Map<String, Object> explain(@RequestParam String buildingId, @RequestParam String date) throws java.io.IOException {
        requireBuilding(buildingId);
        requireDate(date);
        Map<String, Object> production = production();
        if (production == null) {
            throw new IllegalStateException("Рабочая модель ещё не готова.");
        }
        String version = String.valueOf(production.get("version"));
        Map<String, Object> stored = readExplanation(version, buildingId, date);
        if (stored == null) {
            pipeline.run(List.of("explain", "--building", buildingId, "--date", date));
            stored = readExplanation(version, buildingId, date);
        }
        if (stored == null) {
            throw new IllegalArgumentException("Для этой даты нет признаков: слишком ранний день или нет погоды.");
        }
        return stored;
    }

    @PostMapping("/api/ingest")
    public Map<String, Object> ingestDelta() {
        var reports = ingestion.ingest(Path.of(properties.getSources().getDelta()), "delta", false);
        pipeline.run(List.of("score"));
        return Map.of("reports", reports);
    }

    @PostMapping("/api/retrain")
    public Map<String, Object> retrain() {
        pipeline.run(List.of("train"));
        Map<String, Object> production = production();
        List<Map<String, Object>> latest = jdbc.queryForList(
                "SELECT version, status, notes FROM model_versions ORDER BY created_at DESC LIMIT 1");
        return Map.of("production", production == null ? Map.of() : production, "latest", latest.isEmpty() ? Map.of() : latest.get(0));
    }

    @PostMapping("/api/monitor/refresh")
    public Map<String, Object> refreshMonitor() {
        pipeline.run(List.of("monitor"));
        return monitoring();
    }

    @PostMapping("/api/monitor/simulate")
    public Map<String, Object> simulate() {
        pipeline.run(List.of("monitor", "--simulate"));
        return monitoring();
    }

    @PostMapping("/api/models/{version}/promote")
    public Map<String, Object> promote(@PathVariable String version) {
        requireVersion(version);
        admin.promote(version);
        pipeline.run(List.of("score"));
        return Map.of("ok", true);
    }

    @PostMapping("/api/models/{version}/rollback")
    public Map<String, Object> rollback(@PathVariable String version) {
        requireVersion(version);
        admin.rollback(version);
        pipeline.run(List.of("score"));
        return Map.of("ok", true);
    }

    @PostMapping("/api/experiments/{id}/reproduce")
    public Map<String, Object> reproduce(@PathVariable String id) throws java.io.IOException {
        if (!EXPERIMENT.matcher(id).matches()) {
            throw new IllegalArgumentException("Некорректный идентификатор эксперимента.");
        }
        String output = pipeline.run(List.of("reproduce", "--id", id));
        return mapper.readValue(PipelineService.resultLine(output), new TypeReference<>() {});
    }

    private Map<String, Object> production() {
        List<Map<String, Object>> rows = jdbc.queryForList(
                """
                SELECT version, created_at AS createdAt, algorithm, status, data_version AS dataVersion, seed,
                       train_start AS trainStart, train_end AS trainEnd, valid_start AS validStart, valid_end AS validEnd,
                       holdout_start AS holdoutStart, holdout_end AS holdoutEnd, feature_set AS featureSet, params, notes
                FROM model_versions WHERE status = 'production' ORDER BY created_at DESC LIMIT 1
                """);
        return rows.isEmpty() ? null : rows.get(0);
    }

    private List<Map<String, Object>> buildings(Map<String, Object> production) {
        if (production == null) {
            return jdbc.queryForList(
                    """
                    SELECT building_id AS buildingId, address, district, year_built AS yearBuilt,
                           total_area_m2 AS area, wall_type AS wallType, heat_system AS heatSystem, residents, floors
                    FROM buildings ORDER BY building_id
                    """);
        }
        return jdbc.queryForList(
                """
                WITH last_pred AS (
                    SELECT building_id, date, y_true, y_pred,
                           ROW_NUMBER() OVER (PARTITION BY building_id ORDER BY date DESC) AS rn
                    FROM predictions
                    WHERE version = ? AND algorithm = ? AND y_true IS NOT NULL
                )
                SELECT b.building_id AS buildingId, b.address, b.district, b.year_built AS yearBuilt,
                       b.total_area_m2 AS area, b.wall_type AS wallType, b.heat_system AS heatSystem,
                       b.residents, b.floors, p.date AS lastDate, p.y_true AS lastActual, p.y_pred AS lastPred
                FROM buildings b
                LEFT JOIN last_pred p ON p.building_id = b.building_id AND p.rn = 1
                ORDER BY b.building_id
                """,
                production.get("version"), production.get("algorithm"));
    }

    private Map<String, Object> latestMonitoring() {
        List<Map<String, Object>> rows = monitoringSnapshots();
        return rows.isEmpty() ? null : rows.get(0);
    }

    private List<Map<String, Object>> monitoringSnapshots() {
        List<Map<String, Object>> rows = jdbc.queryForList(
                """
                SELECT id, created_at AS createdAt, version, psi_max AS psiMax,
                       psi_alert_features AS psiAlertFeatures, rolling_mae AS rollingMae,
                       reference_mae AS referenceMae, mae_ratio AS maeRatio,
                       drift_alert AS driftAlert, quality_alert AS qualityAlert,
                       retrain_recommended AS retrainRecommended, scenario, details
                FROM monitoring_snapshots ORDER BY id DESC LIMIT 12
                """);
        List<Map<String, Object>> parsed = new ArrayList<>();
        for (Map<String, Object> row : rows) {
            parsed.add(parseSnapshot(row));
        }
        return parsed;
    }

    private Map<String, Object> parseSnapshot(Map<String, Object> row) {
        Map<String, Object> copy = new LinkedHashMap<>(row);
        for (String flag : List.of("driftAlert", "qualityAlert", "retrainRecommended")) {
            Object value = copy.get(flag);
            if (value instanceof Number number) {
                copy.put(flag, number.intValue() == 1);
            }
        }
        Object details = copy.get("details");
        if (details instanceof String text && !text.isBlank()) {
            try {
                copy.put("details", mapper.readValue(text, new TypeReference<Map<String, Object>>() {}));
            } catch (java.io.IOException exception) {
                copy.put("details", Map.of());
            }
        }
        return copy;
    }

    private Map<String, Object> readExplanation(String version, String buildingId, String date) throws java.io.IOException {
        List<Map<String, Object>> rows = jdbc.queryForList(
                """
                SELECT algorithm, y_pred AS prediction, base_value AS base, contributions
                FROM explanations WHERE version = ? AND building_id = ? AND date = ?
                """,
                version, buildingId, date);
        if (rows.isEmpty()) {
            return null;
        }
        Map<String, Object> row = new LinkedHashMap<>(rows.get(0));
        row.put("buildingId", buildingId);
        row.put("date", date);
        row.put("version", version);
        row.put("contributions", mapper.readValue(String.valueOf(row.get("contributions")), new TypeReference<List<Map<String, Object>>>() {}));
        return row;
    }

    private String queryString(String sql) {
        List<Map<String, Object>> rows = jdbc.queryForList(sql);
        if (rows.isEmpty() || rows.get(0).isEmpty()) {
            return null;
        }
        Object value = rows.get(0).values().iterator().next();
        return value == null ? null : String.valueOf(value);
    }

    private int queryInt(String sql) {
        Integer value = jdbc.queryForObject(sql, Integer.class);
        return value == null ? 0 : value;
    }

    private static void requireBuilding(String buildingId) {
        if (buildingId == null || !BUILDING.matcher(buildingId).matches()) {
            throw new IllegalArgumentException("Неизвестный дом.");
        }
    }

    private static void requireDate(String date) {
        if (date == null || !DAY.matcher(date).matches()) {
            throw new IllegalArgumentException("Дата должна быть в формате ГГГГ-ММ-ДД.");
        }
    }

    private static void requireVersion(String version) {
        if (version == null || !VERSION.matcher(version).matches()) {
            throw new IllegalArgumentException("Некорректный номер версии.");
        }
    }
}
