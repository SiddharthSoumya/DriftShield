package com.driftshield.backend.detection.drift;

import com.driftshield.backend.dto.response.DriftDifference;
import com.driftshield.backend.entity.drift.DriftEvent;
import com.driftshield.backend.remediation.drift.AutoRevertService;
import com.driftshield.backend.service.drift.DriftEventService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;

@Component
@RequiredArgsConstructor
@Slf4j
public class DriftDetectionEngine {

    private final YamlConfigLoader yamlConfigLoader;
    private final ConfigComparator configComparator;
    private final DriftEventService driftEventService;
    private final AutoRevertService autoRevertService;

    private static final String BASELINE_PATH =
            "driftshield-data/baseline/baseline.yml";

    private static final String RUNTIME_PATH =
            "driftshield-data/runtime/runtime.yml";

    public List<DriftDifference> detectDrift() {

        Map<String, Object> baseline =
                yamlConfigLoader.loadYaml(BASELINE_PATH);

        Map<String, Object> runtime =
                yamlConfigLoader.loadYaml(RUNTIME_PATH);

        List<DriftDifference> differences =
                configComparator.compareConfigs(
                        baseline,
                        runtime
                );

        if (differences.isEmpty()) {

            log.info(
                    "Infrastructure state verified. No drift found."
            );

            return differences;
        }

        log.warn(
                "Drift detected. Total mismatches found: {}",
                differences.size()
        );

        List<DriftEvent> savedEvents =
                driftEventService.saveDriftEvents(
                        differences
                );

        autoRevertService.autoRestoreInfrastructure(
                baseline,
                savedEvents
        );

        return differences;
    }
}