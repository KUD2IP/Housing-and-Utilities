package ru.housing.forecast;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "app")
public class AppProperties {
    private String python = ".venv/bin/python";
    private final Sources sources = new Sources();
    private final Weather weather = new Weather();

    public String getPython() {
        return python;
    }

    public void setPython(String python) {
        this.python = python;
    }

    public Sources getSources() {
        return sources;
    }

    public Weather getWeather() {
        return weather;
    }

    public static class Sources {
        private String initial = "data/sources/initial";
        private String delta = "data/sources/delta";

        public String getInitial() {
            return initial;
        }

        public void setInitial(String initial) {
            this.initial = initial;
        }

        public String getDelta() {
            return delta;
        }

        public void setDelta(String delta) {
            this.delta = delta;
        }
    }

    public static class Weather {
        private boolean enabled = true;
        private String url = "https://archive-api.open-meteo.com/v1/archive";
        private double latitude = 55.7558;
        private double longitude = 37.6173;
        private int timeoutSeconds = 8;
        private int recentDays = 14;

        public boolean isEnabled() {
            return enabled;
        }

        public void setEnabled(boolean enabled) {
            this.enabled = enabled;
        }

        public String getUrl() {
            return url;
        }

        public void setUrl(String url) {
            this.url = url;
        }

        public double getLatitude() {
            return latitude;
        }

        public void setLatitude(double latitude) {
            this.latitude = latitude;
        }

        public double getLongitude() {
            return longitude;
        }

        public void setLongitude(double longitude) {
            this.longitude = longitude;
        }

        public int getTimeoutSeconds() {
            return timeoutSeconds;
        }

        public void setTimeoutSeconds(int timeoutSeconds) {
            this.timeoutSeconds = timeoutSeconds;
        }

        public int getRecentDays() {
            return recentDays;
        }

        public void setRecentDays(int recentDays) {
            this.recentDays = recentDays;
        }
    }
}
