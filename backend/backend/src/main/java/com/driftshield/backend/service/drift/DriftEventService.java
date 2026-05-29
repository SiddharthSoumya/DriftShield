package com.driftshield.backend.service.drift;


import com.driftshield.backend.dto.response.DriftDifference;
import com.driftshield.backend.dto.response.DriftEventResponse;
import com.driftshield.backend.entity.drift.DriftEvent;
import com.driftshield.backend.repository.drift.DriftEventRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

@Service
@RequiredArgsConstructor
public class DriftEventService {
    private final DriftEventRepository driftEventRepository;

    public List<DriftEvent> saveDriftEvents(
            List<DriftDifference> differences
    ) {
        List<DriftEvent> events =new ArrayList<>();
        for (DriftDifference difference : differences) {
            DriftEvent event = DriftEvent.builder()
                    .configKey(difference.getConfigKey())
                    .oldValue(
                            String.valueOf(
                                    difference.getBaselineValue()
                            )
                    )
                    .newValue(
                            String.valueOf(
                                    difference.getRuntimeValue()
                            )
                    )
                    .severity(calculateSeverity(
                            difference.getConfigKey()
                    ))
                    .reverted(false)
                    .createdAt(LocalDateTime.now())
                    .build();
            DriftEvent savedEvent =
                    driftEventRepository.save(event);
            events.add(savedEvent);
        }
        return events;
    }
    public List<DriftEventResponse> getAllEvents(){

            return driftEventRepository.findAll()
                    .stream()
                    .map(this::mapToResponse)
                    .toList();
    }

    private DriftEventResponse mapToResponse(
            DriftEvent event
    ){
        return DriftEventResponse.builder()
                .id(event.getId())
                .configKey(event.getConfigKey())
                .oldValue(event.getOldValue())
                .newValue(event.getNewValue())
                .severity(event.getSeverity())
                .reverted(event.isReverted())
                .createdAt(event.getCreatedAt())
                .build();
    }

    private String calculateSeverity(
            String configKey
    ){
        return switch (configKey){
            case "firewall_enabled",
                 "public_access",
                 "admin_endpoint_exposed"
                    -> "HIGH";

            case "max_requests_per_minute"
                    -> "MEDIUM";

            default -> "LOW";
        };
    }
}