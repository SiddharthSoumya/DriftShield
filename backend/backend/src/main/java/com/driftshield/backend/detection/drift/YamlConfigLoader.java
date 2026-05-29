package com.driftshield.backend.detection.drift;

import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.yaml.snakeyaml.Yaml;

import java.io.FileInputStream;
import java.io.InputStream;
import java.util.Map;

@Component
@Slf4j
public class YamlConfigLoader {

    private final Yaml yaml = new Yaml();

    public Map<String, Object> loadYaml(String filePath) {

        try (
                InputStream inputStream =
                        new FileInputStream(filePath)
        ) {

            return yaml.load(inputStream);

        } catch (Exception e) {

            log.error(
                    "Failed to load yaml file: {}",
                    filePath
            );

            throw new RuntimeException(
                    "Failed to load yaml file: " + filePath,
                    e
            );
        }
    }
}