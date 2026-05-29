package com.driftshield.backend.controller.api;

import com.driftshield.backend.detection.drift.ConfigComparator;
import com.driftshield.backend.detection.drift.YamlConfigLoader;
import com.driftshield.backend.dto.response.DriftDifference;
import com.driftshield.backend.remediation.drift.ConfigRestoreService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

@RestController
@RequiredArgsConstructor
public class TestController {
    private final YamlConfigLoader yamlConfigLoader;
    private final ConfigComparator configComparator;
    private final ConfigRestoreService configRestoreService;

    @GetMapping("/test-yaml")
    public Map<String, Object> testYaml() {
        return yamlConfigLoader.loadYaml(
                "configs/baseline/baseline.yml"
        );
    }

    @GetMapping("/test-drift")
    public List<DriftDifference> testDrift(){
        Map<String,Object> baseline=
                yamlConfigLoader.loadYaml(
                        "driftshield-data/baseline/baseline.yml"
                );
        Map<String,Object> runtime=
                yamlConfigLoader.loadYaml(
                        "driftshield-data/runtime/runtime.yml"
                );
        return configComparator.compareConfigs(
                baseline,
                runtime
        );
    }

    @GetMapping("/test-restore")
    public String restoreConfig(){
        Map<String,Object> baseline=
                yamlConfigLoader.loadYaml(
                        "configs/baseline/baseline.yml"
                );
        configRestoreService.restoreRuntimeConfig(baseline);
        return "Runtime config restored";
    }
}
