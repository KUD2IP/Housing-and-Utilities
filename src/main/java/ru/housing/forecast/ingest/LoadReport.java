package ru.housing.forecast.ingest;

public record LoadReport(
        String source,
        String status,
        int rowsRead,
        int rowsInserted,
        int rowsSkipped,
        int rowsRejected,
        String message
) {
}
