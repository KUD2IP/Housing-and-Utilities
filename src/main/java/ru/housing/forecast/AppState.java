package ru.housing.forecast;

import org.springframework.stereotype.Component;

@Component
public class AppState {
    private volatile boolean ready;
    private volatile String stage = "запуск";
    private volatile String error;

    public boolean isReady() {
        return ready;
    }

    public void setReady(boolean ready) {
        this.ready = ready;
    }

    public String getStage() {
        return stage;
    }

    public void setStage(String stage) {
        this.stage = stage;
    }

    public String getError() {
        return error;
    }

    public void setError(String error) {
        this.error = error;
    }
}
