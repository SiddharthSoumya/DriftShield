package com.driftshield.backend.scheduler;

import com.driftshield.backend.detection.drift.DriftDetectionEngine;
import com.driftshield.backend.dto.response.DriftDifference;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;

@Component
@RequiredArgsConstructor
@Slf4j
public class DriftMonitoringScheduler {

    private final DriftDetectionEngine driftDetectionEngine;

    @Scheduled(fixedRate = 5000)
    public void monitorInfrastructure() {

        log.info("Running automated drift scan...");

        List<DriftDifference> differences=
            driftDetectionEngine.detectDrift();

        if (differences.isEmpty()) {

            log.info("Infrastructure Secure. No drift detected.");
            return;
        }

        log.warn("Infrastructure drift detected! Total issues found: {}",
                differences.size()
        );

        differences.forEach(difference ->
                log.warn(
                        "Drift Detected -> Config: {} | Excepted :{} | Actual: {}",
                        difference.getConfigKey(),
                        difference.getBaselineValue(),
                        difference.getRuntimeValue()
                )
        );
    }
}