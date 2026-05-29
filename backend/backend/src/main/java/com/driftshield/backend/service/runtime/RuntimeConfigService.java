package com.driftshield.backend.service.runtime;

import com.driftshield.backend.detection.drift.YamlConfigLoader;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.util.Map;

@Service
@RequiredArgsConstructor
public class RuntimeConfigService {

    private static final String RUNTIME_PATH =
            "/app/driftshield-data/runtime/runtime.yml";


    private final YamlConfigLoader yamlConfigLoader;

    public Map<String, Object> getRuntimeConfig() {

        return yamlConfigLoader.loadYaml(
                        RUNTIME_PATH
                );
    }
}