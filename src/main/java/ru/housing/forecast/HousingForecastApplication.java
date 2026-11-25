package ru.housing.forecast;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;

@SpringBootApplication
@EnableConfigurationProperties(AppProperties.class)
public class HousingForecastApplication {
    public static void main(String[] args) {
        SpringApplication.run(HousingForecastApplication.class, args);
    }
}
