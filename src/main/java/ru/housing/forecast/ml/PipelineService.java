package ru.housing.forecast.ml;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;
import org.springframework.stereotype.Service;
import ru.housing.forecast.AppProperties;

@Service
public class PipelineService {
    private final AppProperties properties;

    public PipelineService(AppProperties properties) {
        this.properties = properties;
    }

    public String run(List<String> arguments) {
        Path python = Path.of(properties.getPython());
        if (!python.isAbsolute()) {
            python = Path.of("").toAbsolutePath().resolve(python);
        }
        List<String> command = new ArrayList<>();
        command.add(python.toString());
        command.add("ml/run_pipeline.py");
        command.addAll(arguments);
        ProcessBuilder builder = new ProcessBuilder(command);
        builder.directory(Path.of("").toAbsolutePath().toFile());
        builder.redirectErrorStream(true);
        builder.environment().put("PYTHONIOENCODING", "utf-8");
        builder.environment().put("PYTHONUNBUFFERED", "1");
        try {
            Process process = builder.start();
            StringBuilder output = new StringBuilder();
            Thread reader = new Thread(() -> {
                try (BufferedReader lines = new BufferedReader(new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8))) {
                    String line;
                    while ((line = lines.readLine()) != null) {
                        output.append(line).append('\n');
                        System.out.println(line);
                    }
                } catch (IOException ignored) {
                    output.append("поток вывода модели закрылся\n");
                }
            }, "pipeline-log");
            reader.setDaemon(true);
            reader.start();
            boolean finished = process.waitFor(12, TimeUnit.MINUTES);
            reader.join(2000);
            if (!finished) {
                process.destroyForcibly();
                throw new IllegalStateException("Расчёт модели не завершился за 12 минут.");
            }
            if (process.exitValue() != 0) {
                throw new IllegalStateException(tail(output.toString()));
            }
            return output.toString();
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Расчёт модели прерван.");
        } catch (IOException exception) {
            throw new IllegalStateException("Не удалось запустить Python (" + python + "). Проверьте .venv и README.");
        }
    }

    public static String resultLine(String output) {
        String found = null;
        for (String line : output.split("\n")) {
            if (line.startsWith("RESULT ")) {
                found = line.substring("RESULT ".length());
            }
        }
        if (found == null) {
            throw new IllegalStateException("Модель не вернула результат.");
        }
        return found;
    }

    private static String tail(String output) {
        String[] lines = output.strip().split("\n");
        int from = Math.max(0, lines.length - 8);
        return String.join(" ", java.util.Arrays.copyOfRange(lines, from, lines.length));
    }
}
